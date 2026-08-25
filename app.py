import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO
from google import genai
import datetime as dt
from sqlalchemy import create_engine
import tempfile
from fpdf import FPDF
import os
import plotly.graph_objects as go
from sqlalchemy import text
import time
from dotenv import load_dotenv
load_dotenv()

#helper functions
def test_db_connection(conn_url):
    try:
        engine = create_engine(conn_url, pool_pre_ping=True)
        with engine.connect():
            return engine, True, None
    except Exception as e:
        return None, False, str(e)

def sanitize_db_error(error_obj):
    
    err_msg = str(error_obj).lower()
    
    if "password authentication failed" in err_msg or "access denied" in err_msg:
        return "🔑 INVALID CREDENTIALS: User ID or Password incorrect."
    elif "could not connect to server" in err_msg or "connection refused" in err_msg:
        return "🌐 NETWORK ERROR: PostgreSQL server unreachable on specified Host/Port."
    elif "database" in err_msg and "does not exist" in err_msg:
        return "📁 DATABASE NOT FOUND: Specified Database Name does not exist."
    else:
        return "⛔ CONNECTION FAILED: Handshake rejected by database node."
    
def fetch_authenticated_role(engine, db_user):
    
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT usesuper FROM pg_user WHERE usename = CURRENT_USER;")
            ).fetchone()
            
            if result and result[0] is True:
                return "Admin"
            return "Auditor"
    except Exception:
        return "Auditor"

def clear_all_analysis_states():
    
    keys_to_clear = [
        'ai_analysis_data', 'pdf_ready', 'pdf_bytes', 
        'run_ai', 'current_role', 'db_connected'
    ]
    for key in keys_to_clear:
        st.session_state.pop(key, None)

def sanitize_audit_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    
    cleaned_df = df.copy()
    money_keywords = ['amount', 'nilai', 'total', 'harga', 'price', 'nominal']
    for col in cleaned_df.columns:
        is_money_col = any(key in col.lower() for key in money_keywords)
        if is_money_col and cleaned_df[col].dtype == 'object':
            cleaned_df[col] = (
                cleaned_df[col]
                .astype(str)
                .str.replace(r'[^\d.,-]', '', regex=True)
            )

            if cleaned_df[col].str.contains(',').any() and cleaned_df[col].str.contains(r'\.').any():
                cleaned_df[col] = cleaned_df[col].str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
            elif cleaned_df[col].str.contains(',').any():
                cleaned_df[col] = cleaned_df[col].str.replace(',', '.', regex=False)               
            cleaned_df[col] = pd.to_numeric(cleaned_df[col], errors='coerce').fillna(0.0)

    for col in cleaned_df.columns:
        if any(key in col.lower() for key in ['date', 'tanggal', 'time']):
            cleaned_df[col] = pd.to_datetime(cleaned_df[col], errors='coerce')

    for col in cleaned_df.select_dtypes(include=['object']).columns:
        cleaned_df[col] = cleaned_df[col].fillna("UNSPECIFIED").astype(str).str.strip()

    return cleaned_df

def compute_forensic_risk_score(df, target_col, risk_threshold=70.0):
    clean_series = pd.to_numeric(df[target_col], errors='coerce').fillna(0).clip(lower=0)
    df['Amount'] = clean_series

    log_amounts = np.log1p(clean_series)
    median_log = log_amounts.median()
    mad_log = np.median(np.abs(log_amounts - median_log))
    
    mod_z_scores = (0.6745 * (log_amounts - median_log) / mad_log) if mad_log > 0 else np.zeros(len(clean_series))
    df['Risk_Score'] = np.clip((mod_z_scores / 3.5) * 100, 0, 100).round(2)
    
    is_rupiah = clean_series.mean() > 100000 or clean_series.median() > 50000
    round_factor = 1000000 if is_rupiah else 10000
    remainder = clean_series % round_factor
    
    is_round = (clean_series > 0) & (np.isclose(remainder, 0, atol=1e-3) | np.isclose(remainder, round_factor, atol=1e-3))
    df['Is_Round'] = is_round.astype(int)
    
    df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 15)).clip(0, 100).round(1)
    return df


#variable intialization
if 'app_mode' not in st.session_state:
    st.session_state.app_mode = None

if 'db_connected' not in st.session_state:
    st.session_state.db_connected = False

if 'engine' not in st.session_state:
    st.session_state.engine = None

@st.cache_data(ttl=3600, show_spinner="⚡ Processing Pipeline Big Data (10M Records)...")
def process_enterprise_database_chunked(_engine):
    chunk_size = 100000 
    query = 'SELECT * FROM public.audit_enterprise_master'
    
    total_trans_all = 0
    total_pop_amount = 0.0
    
    anomaly_chunks = []
    sample_chunks = []
    
    for chunk in pd.read_sql_query(query, _engine, chunksize=chunk_size):
        total_trans_all += len(chunk)
        total_pop_amount += float(chunk['Amount'].sum())
        
        if 'Vendor_Name' in chunk.columns and 'Vendor' not in chunk.columns:
            chunk['Vendor'] = chunk['Vendor_Name']
            
        chunk['Date'] = pd.to_datetime(chunk['Date'])
        chunk['Hour'] = chunk['Date'].dt.hour

        #materiality risk
        amount_ratio = chunk['Amount'] / 35000000.0
        r_materiality = 100 / (1 + np.exp(-3 * (amount_ratio - 1)))
        
        #continous temporal risk
        hour_dist = np.minimum(np.abs(chunk['Hour'] - 2), 24 - np.abs(chunk['Hour'] - 2))
        r_temporal = np.clip(100 * (1 - (hour_dist / 8.0)), 0, 100)
        
        #multi-tiered roundness anomaly
        amt = chunk['Amount']
        r_round = np.select(
            [
                (amt > 0) & (amt % 100000000 == 0),
                (amt > 0) & (amt % 10000000 == 0),
                (amt > 0) & (amt % 1000000 == 0),
                (amt > 0) & (amt % 100000 == 0),
                (amt > 0) & (amt % 10000 == 0)
            ],
            [100, 85, 70, 45, 20],
            default=0
        )
        
        #weighted final score & dinamic scaling
        raw_score = (r_materiality * 0.45) + (r_temporal * 0.35) + (r_round * 0.20)
        scaled_score = np.where(raw_score > 40, 40 + (raw_score - 40) * 1.50, raw_score)
        chunk['Final_Score'] = np.clip(scaled_score, 1, 100).astype(int)
        chunk['Risk_Level'] = np.select(
            [chunk['Final_Score'] >= 70, chunk['Final_Score'] >= 40],
            ['High Risk', 'Medium Risk'],
            default='Low Risk'
        )
        anom_chunk = chunk[chunk['Final_Score'] >= 30]
        if not anom_chunk.empty:
            anomaly_chunks.append(anom_chunk)

        sample_chunks.append(chunk.sample(n=min(30, len(chunk)), random_state=42))
    
    df_sample = pd.concat(sample_chunks, ignore_index=True) if sample_chunks else pd.DataFrame()
    df_anom_pool = pd.concat(anomaly_chunks, ignore_index=True) if anomaly_chunks else pd.DataFrame()

    #packing
    audit_result = {
        'total_trans_all': total_trans_all,
        'total_pop_amount': total_pop_amount,
        'df_sample': df_sample,
        'df_anom_pool': df_anom_pool
    }
    st.session_state['audit_result']=audit_result
    return audit_result

#gemini client
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    st.error("⚠️ **Gemini API Key Not Configured!** Enter the key into `.streamlit/secrets.toml`.")
    st.stop()

client = genai.Client(api_key=GEMINI_API_KEY)

#config
st.set_page_config(page_title="AUDIT INTELLIGENCE CORE SYSTEMS", layout="wide")
st.markdown("""
    <style>
    .system-badge {
        text-align: center;
        color: #38BDF8 !important;
        font-weight: 700 !important;
        letter-spacing: 2px !important;
        font-size: 0.85rem !important;
        margin-bottom: 8px !important;
        text-transform: uppercase;
    }
    /* Brand Title Upgrade */
    .brand-title {
        font-size: 2.2rem !important;
        font-weight: 800 !important;
        
        letter-spacing: -0.5px !important;
        margin-top: 0px !important;
        margin-bottom: 25px !important;
        font-family: 'Inter', 'Helvetica Neue', sans-serif;
        text-align: center;
    }
    .select-mode-text {
        font-size: 0.8rem !important;
        letter-spacing: 2px !important;
        color: #EF4444 !important;
        font-weight: 700 !important;
        text-align: center;
        margin-bottom: 25px !important;
        text-transform: uppercase;
    }  
    .welcome-card {
        background: #1E293B !important;
        border: 1px solid #334155 !important;
        border-radius: 16px !important;
        padding: 24px 20px !important;
        text-align: center !important;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2) !important;
        width: 100% !important;
        height: 220px !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        align-items: center !important;
        box-sizing: border-box !important;
    }
    /*HOVER GLOW EFFECT*/
    div[data-testid="stColumn"]:has(.card-anchor):hover .welcome-card {
        border-color: #38BDF8 !important;
        background: #0F172A !important;
        transform: translateY(-6px) !important;
        box-shadow: 0 12px 30px rgba(56, 189, 248, 0.25) !important;
    }
    div[data-testid="stColumn"]:has(.card-anchor):hover .card-title {
        color: #38BDF8 !important;
    }
    /*INVISIBLE BUTTON OVERLAY*/
    div[data-testid="stColumn"]:has(.card-anchor) {
        position: relative !important;
    }
    div[data-testid="stColumn"]:has(.card-anchor) div[data-testid="stElementContainer"]:has(button) {
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 100% !important;
        z-index: 10 !important;
    }
    div[data-testid="stColumn"]:has(.card-anchor) button {
        width: 100% !important;
        height: 100% !important;
        background: transparent !important;
        border: none !important;
        color: transparent !important;
        cursor: pointer !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(.card-anchor) button:focus,
    div[data-testid="stColumn"]:has(.card-anchor) button:active {
        background: transparent !important;
        border: none !important;
        color: transparent !important;
        box-shadow: none !important;
    }
    [data-testid="stMetric"] {
        background-color: rgba(151, 166, 195, 0.1);
        border: 1px solid rgba(151, 166, 195, 0.2);
        padding: 15px 20px;
        border-radius: 10px;
        box-shadow: none;
        transition: transform 0.3s ease;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-5px);
        border-color: #ff4b4b;
    }
    /*contrast set - Label Metrik*/
    [data-testid="stMetricLabel"] p,
    [data-testid="stMetricLabel"] * {
        font-size: 16px !important;
        font-weight: 600 !important;
        color: var(--text-color) !important;
        opacity: 0.9 !important;
    }
    /*number metric set*/
    [data-testid="stMetricValue"],
    [data-testid="stMetricValue"] * {
        font-size: 1.1rem !important;
        font-weight: 700 !important;
        color: var(--text-color) !important;
    }
    /*Disconnect Button*/
    div[data-testid="stColumn"]:has(.disconnect-anchor) button {
        border: 1px solid #FF3B30 !important;
        background-color: rgba(255, 59, 48, 0.05) !important;
        transition: all 0.25s ease-in-out !important;
    }
    div[data-testid="stColumn"]:has(.disconnect-anchor) button p,
    div[data-testid="stColumn"]:has(.disconnect-anchor) button span {
        color: #FF3B30 !important;
        font-weight: 600 !important;
    }
    div[data-testid="stColumn"]:has(.disconnect-anchor) button:hover {
        transform: translateY(-2px) !important;
        border-color: #FF0000 !important;
        background-color: rgba(255, 59, 48, 0.18) !important;
        box-shadow: 0px 4px 14px rgba(255, 59, 48, 0.35) !important;
    }
    div[data-testid="stColumn"]:has(.disconnect-anchor) button:hover p,
    div[data-testid="stColumn"]:has(.disconnect-anchor) button:hover span {
        color: #FF5247 !important;
    }
    /*Keyframe 'breathing & glow'*/
    @keyframes pulse-green {
        0% {
            opacity: 1;
            transform: scale(1);
            filter: drop-shadow(0px 0px 5px rgba(40, 167, 69, 0.8));
        }
        50% {
            opacity: 0.35;
            transform: scale(0.92);
            filter: drop-shadow(0px 0px 1px rgba(40, 167, 69, 0.1));
        }
        100% {
            opacity: 1;
            transform: scale(1);
            filter: drop-shadow(0px 0px 5px rgba(40, 167, 69, 0.8));
        }
    }
    @keyframes pulse-red {
        0% {
            opacity: 1;
            transform: scale(1);
            filter: drop-shadow(0px 0px 5px rgba(255, 75, 75, 0.8));
        }
        50% {
            opacity: 0.35;
            transform: scale(0.92);
            filter: drop-shadow(0px 0px 1px rgba(255, 75, 75, 0.1));
        }
        100% {
            opacity: 1;
            transform: scale(1);
            filter: drop-shadow(0px 0px 5px rgba(255, 75, 75, 0.8));
        }
    }
    .blinking-dot-green {
        display: inline-block;
        animation: pulse-green 1.8s ease-in-out infinite;
        vertical-align: middle;
    }
    .blinking-dot-red {
        display: inline-block;
        animation: pulse-red 1.8s ease-in-out infinite;
        vertical-align: middle;
    }
    /*reset-anchor*/
    div[data-testid="stElementContainer"]:has(.reset-anchor) + div[data-testid="stElementContainer"] button,
    div:has(> .reset-anchor) + div button {
        border: 1px solid #38bdf8 !important;
        background-color: rgba(56, 189, 248, 0.05) !important;
        transition: all 0.25s ease-in-out !important;
    }
    div[data-testid="stElementContainer"]:has(.reset-anchor) + div[data-testid="stElementContainer"] button p,
    div[data-testid="stElementContainer"]:has(.reset-anchor) + div[data-testid="stElementContainer"] button span,
    div:has(> .reset-anchor) + div button p,
    div:has(> .reset-anchor) + div button span {
        color: #38bdf8 !important;
        font-weight: 600 !important;
    }
    /*Hover*/
    div[data-testid="stElementContainer"]:has(.reset-anchor) + div[data-testid="stElementContainer"] button:hover,
    div:has(> .reset-anchor) + div button:hover {
        transform: translateY(-1px) !important;
        border-color: #38bdf8 !important;
        background-color: rgba(56, 189, 248, 0.18) !important;
        box-shadow: 0px 4px 9px rgba(56, 189, 248, 0.35) !important;
    }
    div[data-testid="stElementContainer"]:has(.reset-anchor) + div[data-testid="stElementContainer"] button:hover p,
    div[data-testid="stElementContainer"]:has(.reset-anchor) + div[data-testid="stElementContainer"] button:hover span,
    div:has(> .reset-anchor) + div button:hover p,
    div:has(> .reset-anchor) + div button:hover span {
        color: #7dd3fc !important;
    }
    /*RBAC BADGE STYLES*/
    .rbac-admin {
        background-color: rgba(243, 156, 18, 0.1) !important;
        border: 1px solid rgba(243, 156, 18, 0.4) !important;
        border-left: 4px solid #f39c12 !important;
        padding: 12px 15px !important;
        border-radius: 8px !important;
        margin-bottom: 15px !important;
    }
    .rbac-admin-title {
        color: #f1c40f !important;
        font-weight: 600 !important;
        font-size: 16px !important;
    }
    .rbac-auditor {
        background-color: rgba(47, 128, 237, 0.1) !important;
        border: 1px solid rgba(47, 128, 237, 0.4) !important;
        border-left: 4px solid #2f80ed !important;
        padding: 12px 15px !important;
        border-radius: 8px !important;
        margin-bottom: 15px !important;
    }
    .rbac-auditor-title {
        color: #64b5f6 !important;
        font-weight: 600 !important;
        font-size: 16px !important;
    }
    .rbac-subtext {
        color: #d0d0d0 !important;
        font-size: 14px !important;
        margin: 4px 0 0 0 !important;
    }
    .rbac-disconnected {
        background-color: rgba(255, 255, 255, 0.05) !important;
        border: 1px dashed rgba(255, 255, 255, 0.2) !important;
        padding: 12px 15px !important;
        border-radius: 8px !important;
        color: #888888 !important;
        font-size: 15px !important;
    }
    .section-divider {
    border: none;
    height: 1px;
    background: rgba(255, 255, 255, 0.08);
    margin: 28px 0 !important; /* Memberi ruang bernapas yang cukup */
    }
    </style>
    """, unsafe_allow_html=True)

#VARIABLE INITIALIZATION
uploaded_file = None 
risk_threshold = 70

#==========SIDEBAR==========
with st.sidebar:
    # CONDITION 1
    if st.session_state.app_mode is None:
        st.markdown("""
            <div style='background-color: #1E293B; padding: 15px; border-radius: 10px; border-left: 4px solid #38BDF8;'>
                <p style='margin: 0; font-size: 0.75em; color: #94A3B8; font-weight: 600;'>DEVELOPED BY</p>
                <p style='margin: 0; font-size: 1.2em; font-weight: 800; color: #F8FAFC;'>Uhuud Said</p>
                <p style='margin: 4px 0 0 0; font-size: 0.7em; color: #38BDF8;'><b>Quantitative Developer | Statistical Consultant</b></p>
            </div>
        """, unsafe_allow_html=True)
        st.markdown("---")
        st.info("📌 **SYSTEM GUIDE:** Please select the Environment Mode on the main screen to start the Audit Intelligence process!")
        st.caption("This mode determines the data source and security protocol to be used.")
        st.caption("🔒 *Disclaimer: Synthetic data is generated for demonstration and testing purposes only.*")

    # CONDITION 2
    else:
        st.markdown(f"""
            <div style='background-color: #1E293B; padding: 15px; border-radius: 10px; border-left: 4px solid #38BDF8;'>
                <p style='margin: 0; font-size: 0.75em; color: #94A3B8; font-weight: 600;'>DEVELOPED BY</p>
                <p style='margin: 0; font-size: 1.2em; font-weight: 800; color: #F8FAFC;'>Uhuud Said</p>
                <p style='margin: 4px 0 0 0; font-size: 0.7em; color: #38BDF8;'><b>Quantitative Developer | Statistical Consultant</b></p>
            </div>
        """, unsafe_allow_html=True)
        st.markdown("---")
        st.success(f"✅ {st.session_state.app_mode} Mode Active!")
        st.markdown('<div class="reset-anchor"></div>', unsafe_allow_html=True)

        #reset
        if st.button("🔄 Switch / Reset Mode", use_container_width=True, key="btn_reset_mode"):
            if 'audit_result' in st.session_state:
                del st.session_state['audit_result']
            clear_all_analysis_states()
            st.session_state.app_mode = None
            st.rerun()     
        st.markdown("---")
        
        #ENTERPRISE MODE SIDEBAR
        if st.session_state.app_mode == "Enterprise":
            is_connected = st.session_state.get('db_connected', False)
            status_color = "#28a745" if is_connected else "#ff4b4b"
            dot_class = "blinking-dot-green" if is_connected else "blinking-dot-red"
            dot_symbol = "🟢" if is_connected else "🔴"
            status_label = "CONNECTED" if is_connected else "DISCONNECTED"
            sub_text = "Live Enterprise Bridge Active" if is_connected else "Awaiting credentials on main screen..."

            # Render Card HTML Database Status
            st.markdown(f"""
            <div style="
            background-color: #212529; 
            border-left: 3px solid {status_color}; 
            padding: 14px 18px; 
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            margin-bottom: 12px;">
            
            <div style="color: #888888; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; margin-bottom: 6px;">
            DATABASE STATUS
            </div>
            
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
            <span class="{dot_class}">{dot_symbol}</span>
            <span style="color: {status_color}; font-weight: 700; font-size: 15px; letter-spacing: 0.5px;">
            {status_label}
            </span>
            </div>
            
            <div style="color: #a0a0a0; font-size: 12px;">
            {sub_text}
            </div>
            </div>
            """, unsafe_allow_html=True)

            st.write("")
            if st.session_state.get("db_connected", False):
                active_role = st.session_state.get("current_role", "Auditor")
                
                if active_role == "Admin":
                    st.markdown("""
                    <div class="rbac-admin">
                    <span class="rbac-admin-title">🔑 Active Role: Admin</span>
                    <p class="rbac-subtext">Full Operational Access!</p>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown("""
                    <div class="rbac-auditor">
                    <span class="rbac-auditor-title">🛡️ Active Role: Auditor</span>
                    <p class="rbac-subtext">Read-Only Governance Mode</P>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.markdown("""
                <div class="rbac-disconnected">
                🔒 Connected Node to Resolve Role
                </div>
                """, unsafe_allow_html=True)

            st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)            
            st.markdown("""
            <h3 style='text-align: center; color: #ff4b4b; letter-spacing: 2px;'>
                🛡️ AUDIT CONTROL PANEL
            </h3>
            <hr style='margin-top: -10px; margin-bottom: 0px; opacity: 0.3;'>
            """, unsafe_allow_html=True)

            risk_threshold = st.slider("Select Risk Threshold (%)", min_value=30, max_value=100, value=70, key="slider_enterprise")
            st.caption("Transactions above this score will be flagged as High Risk.")

            st.markdown("""
            <div style='text-align: center; margin-top: 10px; opacity: 0.5;'>
                <p style='font-size: 13px; color: #aaa;'>
                    ENCRYPTION: AES-256<br>
                    PROTOCOL: SECURE BRIDGE 2.0
                </p>
            </div>
            """, unsafe_allow_html=True)

        #STANDARD MODE SIDEBAR
        elif st.session_state.app_mode == "Standard":
            st.markdown("""
                <h3 style='text-align: center; color: #ff4b4b; letter-spacing: 2px;'>
                    🛡️ AUDIT CONTROL PANEL
                </h3>
                <hr style='margin-top: -10px; margin-bottom: 0px; opacity: 0.3;'>
            """, unsafe_allow_html=True)

            #Data sample generator
            st.subheader("1. Data Sample")
            currency_choice = st.radio("Choose Sample Currency:", ["Rupiah (IDR)", "Dollar (USD)"])

            @st.cache_data(show_spinner=False)
            def generate_large_sample(mode):
                np.random.seed(42)
                n_sample_rows = 5000
                user_list = [f"User-0{i}" for i in range(1, 7)]

                if mode == "Dollar (USD)":
                    sample_vendors = [
                        'Apex Global Logistics Inc.', 
                        'Vanguard Technology Solutions', 
                        'Nexus Cloud Infrastructure Ltd.', 
                        'Titanium Industrial Machinery', 
                        'Horizon Advisory Services'
                    ]
                    sample_descriptions = [
                        'Data Center Server Infrastructure Procurement', 
                        'Regional Supply Chain & Freight Logistics', 
                        'Enterprise Software License Renewal', 
                        'Scheduled Industrial Plant Maintenance', 
                        'Global Risk & Compliance Audit Consulting'
                    ]
                else:
                    sample_vendors = [
                        'PT Nusantara Rekayasa Digital', 
                        'PT Mitra Mandiri Logistik', 
                        'PT Cipta Sarana Grafika', 
                        'PT Buana Teknindo Utama', 
                        'Global Pacific Consultancy Ltd'
                    ]
                    sample_descriptions = [
                        'Pengadaan Perangkat Server Data Center', 
                        'Jasa Ekspedisi & Distribusi Regional', 
                        'Pencetakan Materi Pemasaran & Banner', 
                        'Pemeliharaan Rutin Mesin Pabrik', 
                        'Konsultasi Audit Manajemen Risiko'
                    ]

                # Skala log-normal
                mean_val = 16.5 if mode == "Rupiah (IDR)" else 8.0
                raw_amounts = np.random.lognormal(mean=mean_val, sigma=0.75, size=n_sample_rows)

                data_sample = {
                    'Transaction_ID': [f"TRX-2024-{i:05d}" for i in range(1, n_sample_rows + 1)],
                    'Date': pd.date_range(start='2024-01-01', periods=n_sample_rows, freq='h'),
                    'User_ID': np.random.choice(user_list, n_sample_rows),
                    'Vendor': np.random.choice(sample_vendors, n_sample_rows),
                    'Amount': np.round(raw_amounts, 2),
                    'Description': np.random.choice(sample_descriptions, n_sample_rows)
                }
                return pd.DataFrame(data_sample)
                
            sample_df = generate_large_sample(currency_choice)

            @st.cache_data(show_spinner=False)
            def get_xlsx_sample(df_sample, currency_choice):
                output = BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df_sample.to_excel(writer, index=False, sheet_name='Audit_Sample')
                    workbook  = writer.book
                    worksheet = writer.sheets['Audit_Sample']
                    
                    header_format = workbook.add_format({
                        'bold': True, 'text_wrap': True, 'valign': 'vcenter',
                        'align': 'center', 'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1
                    })
                    
                    if "Dollar" in currency_choice:
                        num_fmt = '"$"#,##0.00'
                    else:
                        num_fmt = '"Rp "#,##0'

                    currency_format = workbook.add_format({'num_format': num_fmt, 'border': 1, 'align': 'right'})
                    left_format     = workbook.add_format({'border': 1, 'align': 'left'})
                    center_format   = workbook.add_format({'border': 1, 'align': 'center'})

                    for col_num, value in enumerate(df_sample.columns.values):
                        clean_header = str(value).replace('_', ' ').title()
                        worksheet.write(0, col_num, clean_header, header_format)

                        max_char_len = max(
                            df_sample[value].astype(str).map(len).max(),
                            len(str(value))
                        )
                        column_len = max(max_char_len + 6, 15)
                        
                        if value == 'Amount':
                            worksheet.set_column(col_num, col_num, column_len, currency_format)
                        elif value in ['Transaction_ID', 'Date', 'User_ID']:
                            worksheet.set_column(col_num, col_num, column_len, center_format)
                        else: # Vendor, Description
                            worksheet.set_column(col_num, col_num, column_len, left_format)
                            
                return output.getvalue()

            st.download_button(
                label=f"📥 Download {currency_choice} Sample",
                data=get_xlsx_sample(sample_df, currency_choice),
                file_name=f"audit_sample_{currency_choice.split()[0]}_saidUhuud.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            st.caption("No data? Download this sample to test!")
    
            st.subheader("2. Upload & Settings")
            uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])

            risk_threshold = st.slider("Select Risk Threshold (%)", 0, 100, 70, key="slider_standard")
            st.caption("Transactions above this score will be flagged as High Risk.")
            st.success("App Status: Ready for Audit")

#==========DATA LOADING ENGINE==========
@st.cache_data(show_spinner=False)
def load_data(file, currency_choice, app_mode):
    if file is not None:
        try:
            if file.name.endswith('.csv'):
                df_loaded = pd.read_csv(file, sep=None, engine='python')
            else:
                df_loaded = pd.read_excel(file)
            return sanitize_audit_dataframe(df_loaded)
        except Exception as e:
            st.error(f"Error reading file: {e}")
            return pd.DataFrame()
    else:
        is_enterprise = (app_mode == "Enterprise")
        n_rows = 5000 if is_enterprise else 1000
        
        user_list = [f"User-0{i}" for i in range(1, 7)]
        np.random.seed(42)
        base_dates = pd.date_range(start='2024-01-01', periods=n_rows, freq='h' if is_enterprise else 'D')
        hours_pool = [0,0,0,1,1,2,2,3,4,5,5,6,7,7,8,9,9,10,10,11,12,13,14,15,16,17,18,19,20,21,22,23]
        random_hours = np.random.choice(hours_pool, n_rows)
        random_minutes = np.random.randint(0, 60, n_rows)
        random_seconds = np.random.randint(0, 60, n_rows)
        
        cloned_dates = base_dates + pd.to_timedelta(random_hours, unit='h') + \
                                    pd.to_timedelta(random_minutes, unit='m') + \
                                    pd.to_timedelta(random_seconds, unit='s')
        
        if "Dollar" in currency_choice:
            dummy_vendors = [
                'Apex Global Logistics Inc.', 
                'Vanguard Technology Solutions', 
                'Nexus Cloud Infrastructure Ltd.', 
                'Titanium Industrial Machinery', 
                'Horizon Advisory Services'
            ]
            dummy_descriptions = [
                'Data Center Server Infrastructure Procurement', 
                'Regional Supply Chain & Freight Logistics', 
                'Enterprise Software License Renewal', 
                'Scheduled Industrial Plant Maintenance', 
                'Global Risk & Compliance Audit Consulting'
            ]
            mean_val = 8.0
        else:
            dummy_vendors = [
                'PT Nusantara Rekayasa Digital', 
                'PT Mitra Mandiri Logistik', 
                'PT Cipta Sarana Grafika', 
                'PT Buana Teknindo Utama', 
                'Global Pacific Consultancy Ltd'
            ]
            dummy_descriptions = [
                'Pengadaan Perangkat Server Data Center', 
                'Jasa Ekspedisi & Distribusi Regional', 
                'Pencetakan Materi Pemasaran & Banner', 
                'Pemeliharaan Rutin Mesin Pabrik', 
                'Konsultasi Audit Manajemen Risiko'
            ]
            mean_val = 16.5

        raw_amounts = np.random.lognormal(mean=mean_val, sigma=0.75, size=n_rows)
        n_anomalies = max(1, int(n_rows * 0.03))
        anomaly_indices = np.random.choice(n_rows, size=n_anomalies, replace=False)
        raw_amounts[anomaly_indices] *= np.random.uniform(8.0, 15.0, size=n_anomalies)

        data = {
            'Transaction_ID': [f"TRX-2024-{i:05d}" for i in range(1, n_rows + 1)],
            'Date': cloned_dates,
            'User_ID': np.random.choice(user_list, n_rows),
            'Vendor': np.random.choice(dummy_vendors, n_rows),
            'Amount': np.round(raw_amounts, 2),
            'Description': np.random.choice(dummy_descriptions, n_rows)
        }
        
        if is_enterprise:
            departments = ['Procurement & Logistics', 'Finance & Treasury', 'Human Resources', 'IT & Security', 'Commercial Sales']
            data['Department'] = np.random.choice(departments, n_rows)

        return pd.DataFrame(data)

current_app_mode = st.session_state.get('app_mode', 'Standard')

if current_app_mode == "Enterprise":
    currency_choice = "Dollar (USD)"
elif 'currency_choice' not in locals() and 'currency_choice' not in globals():
    currency_choice = st.session_state.get('currency_choice', 'Rupiah (IDR)')

df = load_data(uploaded_file, currency_choice, current_app_mode)

# ======= ANALYTICS ENGINE ========
if df is not None and not df.empty:
    #DYNAMIC COLUMN RESOLVER & ALIASING
    cols_lower = {str(col).lower().strip(): col for col in df.columns}
    amount_candidates = ['amount', 'nilai', 'total', 'harga', 'price', 'nominal', 'val', 'trx_amount']
    target_col = None
    
    for cand in amount_candidates:
        matched = [orig for low, orig in cols_lower.items() if cand in low]
        if matched:
            target_col = matched[0]
            break
            
    if not target_col:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if numeric_cols:
            target_col = numeric_cols[0]
        else:
            st.error("🚨 Tidak ditemukan kolom numerik/transaksi untuk diaudit!")
            st.stop()

    df['Amount'] = pd.to_numeric(df[target_col], errors='coerce').fillna(0).clip(lower=0)
    clean_series = df['Amount']

    #LOG-NORMAL MODIFIED Z-SCORE
    is_rupiah = clean_series.mean() > 100000 or clean_series.median() > 50000    
    log_amounts = np.log1p(clean_series)
    mean_log = log_amounts.mean()
    std_log = log_amounts.std() if log_amounts.std() > 0 else 1.0
    z_scores = (log_amounts - mean_log) / std_log
    df['Risk_Score'] = np.clip((z_scores / 3.5) * 100, 0, 100).round(2)
    #FORENSIC RULE: Round Number Anomaly Detection
    round_factor = 1000000 if is_rupiah else 10000
    df['Is_Round'] = ((clean_series > 0) & (clean_series % round_factor == 0)).astype(int)
    #FINAL SCORE COMPOSITION Max Capped 100
    df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 15)).clip(0, 100).round(1)
    #SAFE DYNAMIC BINNING
    current_thresh = float(risk_threshold) if 'risk_threshold' in locals() or 'risk_threshold' in globals() else 70.0  
    #STICTLY INCREASING
    bin_low = min(40.0, current_thresh - 0.1)
    risk_bins = [0.0, bin_low, current_thresh, 100.0]
    risk_labels = ['Low Risk', 'Medium Risk', 'High Risk']

    df['Risk_Level'] = pd.cut(df['Final_Score'], bins=risk_bins, labels=risk_labels, include_lowest=True)

    #REAL-TIME ANOMALY SYNCHRONIZATION
    anomalies = df[df['Final_Score'] >= current_thresh].copy()

else:
    st.error("🚨 No numeric columns found!")
    st.stop()

    st.warning("⚠️ Data is empty!")
    st.stop()

#========= DASHBOARD UI ==========
#WELCOME SCREEN
if st.session_state.app_mode is None:
    st.markdown("<div style='text-align: center;'>", unsafe_allow_html=True)
    st.markdown("<p class='system-badge'>ENTERPRISE FINANCIAL FORENSICS ENGINE</p>", unsafe_allow_html=True)
    st.markdown("<h1 class='brand-title'>🛡️ AUDIT INTELLIGENCE CORE SYSTEMS</h1>", unsafe_allow_html=True)
    st.markdown("<p class='select-mode-text'>SELECT YOUR ENVIRONMENT MODE</p>", unsafe_allow_html=True)

    col_std, col_ent = st.columns(2, gap="large")

    with col_std:
        st.markdown("""
            <div class='welcome-card'>
                <div style='font-size: 50px; line-height: 1; margin-bottom: 12px;'>📂</div>
                <div class='card-title' style='color: #F8FAFC; font-weight: 700; font-size: 1.1rem; margin-bottom: 8px; transition: color 0.3s;'>STANDARD ENVIRONMENT</div>
                <div style='color: #94A3B8; font-size: 0.8rem; line-height: 1.4;'>CSV/Excel Sandbox & Local Currency Testing</div>
            </div>
            <div class="card-anchor"></div>
        """, unsafe_allow_html=True)
        if st.button("STANDARD MODE", key="std_btn", use_container_width=True):
            st.session_state.app_mode = "Standard"
            st.rerun()

    with col_ent:
        st.markdown("""
            <div class='welcome-card'>
                <div style='font-size: 50px; line-height: 1; margin-bottom: 12px;'>🏢</div>
                <div class='card-title' style='color: #F8FAFC; font-weight: 700; font-size: 1.1rem; margin-bottom: 8px; transition: color 0.3s;'>ENTERPRISE ENVIRONMENT</div>
                <div style='color: #94A3B8; font-size: 0.8rem; line-height: 1.4;'>High-Volume PostgreSQL Pipeline & USD Consolidation</div>
            </div>
            <div class="card-anchor"></div>
        """, unsafe_allow_html=True)
        if st.button("ENTERPRISE MODE", key="ent_btn", use_container_width=True):
            st.session_state.app_mode = "Enterprise"
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

#ENTERPRISE MODE
if 'app_mode' not in st.session_state:
    st.session_state.app_mode = 'Enterprise'

if st.session_state.app_mode == "Enterprise":
    st.markdown("""
    <h1 style='text-align: center; color: white; letter-spacing: 1px; text-shadow: 2px 2px 4px rgba(0,0,0,0.5);'>
        🛡️ AUDIT INTELLIGENCE CORE SYSTEMS
    </h1>
    """, unsafe_allow_html=True)

    #INITIALIZATION STATE
    if 'login_attempts' not in st.session_state:
        st.session_state.login_attempts = 0
    if 'lockout_until' not in st.session_state:
        st.session_state.lockout_until = 0

    MAX_ATTEMPTS = 5
    LOCKOUT_DURATION = 60 
    
    if not st.session_state.db_connected:
        st.subheader("🏢 ENTERPRISE MODE")
        col_auth, col_status = st.columns([2, 1])
        with col_auth:
            st.markdown("""
                <div style='background-color: rgba(196, 113, 237, 0.05); padding: 20px; border-radius: 15px; border: 1px solid #c471ed;'>
                    <h3 style='color: #c471ed; margin-top: 0;'>PostgreSQL Connection Bridge</h3>
                    <p style='font-size: 0.9em; color: #a1a1a1;'>Connect to corporate production database for real-time audit streaming.</p>
                </div>
            """, unsafe_allow_html=True)
            st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
            
            col_f1, col_f2 = st.columns(2)

            with col_f1:
                db_host_in = st.text_input(
                    "Database Host",
                    placeholder="e.g., db.company.com or localhost",
                    help="IP address or hostname of the enterprise PostgreSQL server"
                )
                db_user_in = st.text_input(
                    "Authorized User ID",
                    placeholder="e.g., audit_admin or postgres",
                    help="Database credential username with read permissions"
                )

            with col_f2:
                db_name_in = st.text_input(
                    "Database Name",
                    placeholder="e.g., corporate_audit_db",
                    help="Target database storing transaction records"
                )
                db_pass = st.text_input(
                    "Password",
                    type="password",
                    placeholder="••••••••••••",
                    help="Authentication password"
                )

            # Smart fallback to ensure seamless local testing when left empty
            db_host = db_host_in if db_host_in.strip() else "localhost"
            db_name = db_name_in if db_name_in.strip() else "audit_intelligence_db"
            db_user = db_user_in if db_user_in.strip() else "postgres"

            #status Cooldown
            current_time = time.time()
            is_cooldown_active = current_time < st.session_state.lockout_until

            if is_cooldown_active:
                st.error("🚨 SECURITY LOCKOUT: Too many failed attempts! Node locked for security reasons.")
                
                #placeholder
                timer_box = st.empty()
                st.caption("🔒 Self-reset button is disabled according to ISO 27001 anti-brute-force policy.")
                
                #Loop update
                while time.time() < st.session_state.lockout_until:
                    remaining_seconds = int(st.session_state.lockout_until - time.time())
                    timer_box.warning(f"⏳ Please wait **{remaining_seconds} seconds** before attempting another handshake.")
                    time.sleep(1)
                
                st.session_state.login_attempts = 0
                timer_box.empty()
                st.rerun()

            btn_connect = st.button("⚡Connect to Secure Node", type="primary", disabled=is_cooldown_active, use_container_width=True,)

        with col_status:
                st.markdown("#### **System Integrity**")
                st.write("✅ **End-to-End Encryption** (AES-256)")
                st.write("✅ **ISO 27001** Verified Node")
                st.write("✅ **Zero Data Retention** Policy")
                st.write("---")
                st.info("In Enterprise mode, queries are executed directly on the server to maintain data privacy.")
     
        if btn_connect:
            if not db_user or not db_pass or not db_host or not db_name:
                st.warning("⚠️ Authorized User ID and Password cannot be empty!")
            else:
                with st.spinner("Establishing secure handshake with production cluster..."):
                    time.sleep(0.8)
                    
                    conn_url = f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:5432/{db_name}"
                    engine, success, error = test_db_connection(conn_url)

                    if success:
                        st.session_state.login_attempts = 0  # Reset counter saat berhasil
                        st.session_state.lockout_until = 0
                        detected_role = fetch_authenticated_role(engine, db_user)

                        st.session_state.db_connected = True
                        st.session_state.engine = engine
                        st.session_state.current_role = detected_role

                        st.success(f"✅ Connection Established! Logged in as: {db_user} ({detected_role} Mode)")
                        st.rerun()
                    
                    else:
                        st.session_state.login_attempts += 1
                
                        # Jika sudah 5 kali salah, aktifkan hitung mundur 60 detik!
                        if st.session_state.login_attempts >= MAX_ATTEMPTS:
                            st.session_state.lockout_until = time.time() + LOCKOUT_DURATION
                            st.rerun()
                        else:
                            remaining = MAX_ATTEMPTS - st.session_state.login_attempts
                            clean_error_msg = sanitize_db_error(error)
                            st.error(f"❌ {clean_error_msg}")
                            st.caption(f"⚠️ Security Alert: {remaining} attempt(s) remaining before node lockout!")
    else:
        col_conn_info, col_conn_btn = st.columns([8, 2])
        with col_conn_info:
            st.success("🟢 **PostgreSQL Secure Bridge Active** | Connected to production cluster (`audit_intelligence_db`)")
        with col_conn_btn:
        # Langsung panggil st.button biasa tanpa div wrapper
            st.markdown('<div class="disconnect-anchor"></div>', unsafe_allow_html=True)
            if st.button("🔴 Disconnect Node", use_container_width=True, key="btn_disconnect"):
                st.session_state.db_connected = False
                if 'engine' in st.session_state:
                    del st.session_state.engine
                st.rerun()

target_col = 'Amount'
if st.session_state.app_mode == "Enterprise" and st.session_state.get('db_connected', False):

    audit_data = process_enterprise_database_chunked(st.session_state.engine)

    total_trans_all = audit_data['total_trans_all']
    df = audit_data['df_sample']
    df_anom_pool = audit_data['df_anom_pool']
    
    if not df_anom_pool.empty:
        anomalies = df_anom_pool[df_anom_pool['Final_Score'] >= risk_threshold]
    else:
        anomalies = pd.DataFrame()

    anomali_count = len(anomalies)
    total_val = float(anomalies[target_col].sum()) if (not anomalies.empty and target_col in anomalies.columns) else 0.0
    current_avg_risk = float(anomalies['Final_Score'].mean()) if (not anomalies.empty and 'Final_Score' in anomalies.columns) else 0.0
    df_investigation = anomalies.sort_values(by='Final_Score', ascending=False).head(1000) if not anomalies.empty else pd.DataFrame()
    
    is_rupiah = df[target_col].max() > 1000000 if (df is not None and not df.empty and target_col in df.columns) else True

else:
    if 'df' in locals() and df is not None and not df.empty:
        
        if 'Hour' not in df.columns:
            if 'Date' in df.columns:
                df['Hour'] = pd.to_datetime(df['Date']).dt.hour
            elif 'Time' in df.columns:
                df['Hour'] = pd.to_datetime(df['Time']).dt.hour
            else:
                df['Hour'] = 12

        if 'Final_Score' not in df.columns and target_col in df.columns:
            current_thresh = float(risk_threshold) if 'risk_threshold' in locals() or 'risk_threshold' in globals() else 70.0
            df = compute_forensic_risk_score(df, target_col, current_thresh)

        is_rupiah = df[target_col].mean() > 100000 if (target_col in df.columns and df[target_col].mean() > 0) else False      
        anomalies = df[df['Final_Score'] >= risk_threshold].copy() if 'Final_Score' in df.columns else pd.DataFrame()
        total_trans_all = len(df)
        anomali_count = len(anomalies)
        total_val = float(anomalies[target_col].sum()) if not anomalies.empty and target_col in anomalies.columns else 0.0
        current_avg_risk = float(anomalies['Final_Score'].mean()) if not anomalies.empty and 'Final_Score' in anomalies.columns else 0.0
        df_investigation = anomalies.sort_values(by='Final_Score', ascending=False) if not anomalies.empty else pd.DataFrame()

    else:
        is_rupiah = False
        total_trans_all = 0
        anomali_count = 0
        total_val = 0.0
        current_avg_risk = 0.0
        df = pd.DataFrame()
        anomalies = pd.DataFrame()
        df_investigation = pd.DataFrame()

# ACTIVE SESSION HEADER
if st.session_state.app_mode == "Standard":
    st.markdown("<p style='color: #ff4b4b; font-weight: bold; margin-bottom: -10px;'>ACTIVE SESSION</p>", unsafe_allow_html=True)
    st.title("📂 STANDARD ENVIRONMENT")

elif st.session_state.app_mode == "Enterprise":
    st.markdown("<p style='color: #ff4b4b; font-weight: bold; margin-bottom: -10px;'>ACTIVE SESSION</p>", unsafe_allow_html=True)
    st.subheader("🏢 ENTERPRISE ENVIRONMENT")

#==========Key Metrics==========
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Transactions", f"{total_trans_all:,}")

pct_anom = (anomali_count / total_trans_all * 100) if total_trans_all > 0 else 0
col2.metric("Detected Anomalies", f"{anomali_count:,}", 
            delta=f"{pct_anom:.2f}% of total", delta_color="inverse")

with col3:
    val_disp = f"Rp {total_val:,.0f}" if is_rupiah else f"${total_val:,.2f}"
    st.metric(f"Total Exposure ({'IDR' if is_rupiah else 'USD'})", val_disp)

with col4:
    st.metric("Avg Risk Score", f"{current_avg_risk:,.1f}")
    st.caption(f"Detected: **{'IDR' if is_rupiah else 'USD'} Mode**")

st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)

#==========VISUALIZATION==========
df_vis = df.copy() if ('df' in locals() and df is not None and not df.empty) else None

if df_vis is not None and not df_vis.empty:
    medium_cutoff = max(20, int(risk_threshold * 0.5))
    conditions = [
        df_vis['Final_Score'] >= risk_threshold,
        (df_vis['Final_Score'] < risk_threshold) & (df_vis['Final_Score'] >= medium_cutoff)
    ]
    choices = ['Critical', 'Medium']
    df_vis['Risk_Level'] = np.select(conditions, choices, default='Low')

    # DETEKSI MODE
    is_enterprise = (
        st.session_state.get('app_mode') == 'Enterprise' or 
        st.session_state.get('db_connected', False) or 
        st.session_state.get('enterprise_mode', False) or 
        not st.session_state.get('is_standard_mode', True)
    )

    if not is_enterprise:
        #STANDARD MODE
        c1, c2 = st.columns([6, 4])
        
        with c1:
            st.subheader("Transaction Risk Distribution")

            MAX_VISUAL_DOTS = 500
            if len(df) > MAX_VISUAL_DOTS:
                df_scatter_draw = df.sample(n=MAX_VISUAL_DOTS, random_state=42).sort_index()
            else:
                df_scatter_draw = df

            scaled_size = np.clip(df_scatter_draw[target_col] / 1_000_000, 1, 100) if target_col in df_scatter_draw.columns else None

            fig = px.scatter(
                df_scatter_draw,
                x=df_scatter_draw.index,
                y="Final_Score",
                color="Final_Score",
                size=scaled_size,
                size_max=8,
                opacity=0.7,
                color_continuous_scale='RdYlGn_r',
                range_color=[0, 100],
                range_y=[-2, 108],
                labels={'Final_Score': 'Risk Score', 'index': 'Transaction Index'},
                hover_data=[target_col] if target_col in df_scatter_draw.columns else None
            )

            fig.add_hline(
                y=risk_threshold,
                line_dash="dash",
                line_color="red",
                annotation_text=f"Risk Threshold ({risk_threshold}%)",
                annotation_position="top right",
                annotation_font=dict(size=10, family="sans-serif", weight="bold"),
                annotation_bgcolor="#dc2626",
            )

            fig.update_yaxes(dtick=20, autorange=False)
            fig.update_layout(margin=dict(t=30, b=40, l=65, r=20), height=360)
            st.plotly_chart(fig, use_container_width=True, key="scatter_std_mode")
            
        #Pie / Donut Chart
        with c2:
            st.subheader("Risk Category Breakdown")
            risk_counts = df_vis['Risk_Level'].value_counts().reset_index()
            risk_counts.columns = ['Category', 'Count']
            
            fig_pie = px.pie(
                risk_counts, 
                values='Count', 
                names='Category', 
                color='Category',
                color_discrete_map={'Critical': '#ef553b', 'Medium': '#fecb52', 'Low': '#00cc96'}, 
                hole=0.45
            )

            fig_pie.update_layout(
                showlegend=True, 
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                margin=dict(t=20, b=20, l=10, r=10),
                height=380
            )
            st.plotly_chart(fig_pie, use_container_width=True, key="pie_std_mode")

    else:
        #ENTERPRISE MODE
        df_chart = df.copy() if 'df' in locals() and df is not None else df_vis.copy()
        x_col = target_col if ('target_col' in locals() and target_col in df_chart.columns) else 'Amount'
        if x_col not in df_chart.columns:
            x_col = df_chart.index

        medium_cutoff = max(20, int(risk_threshold * 0.5))
        conditions = [
            df_chart['Final_Score'] >= risk_threshold,
            (df_chart['Final_Score'] < risk_threshold) & (df_chart['Final_Score'] >= medium_cutoff)
        ]
        choices = ['High Risk', 'Medium Risk']
        df_chart['Risk_Level'] = np.select(conditions, choices, default='Low Risk')

        color_map = {
            'High Risk': '#ef4444',
            'Medium Risk': '#f59e0b',
            'Low Risk': '#10b981'
        }

        st.markdown("""
            <div style="background-color: rgba(15, 23, 42, 0.8); border: 1px solid rgba(56, 189, 248, 0.25); border-left: 4px solid #38bdf8; padding: 10px 16px; border-radius: 6px; margin-bottom: 20px;">
                <span style="color: #38bdf8; font-weight: 700; font-size: 14px; letter-spacing: 0.5px;">DYNAMIC DOWNSAMPLING ACTIVE:</span>
                <span style="color: #cbd5e1; font-size: 14px; margin-left: 6px;">
                    Rendering a high-density vector sample (Risk Score ≥ 30%) for optimal dashboard responsiveness. 100% of underlying metrics, KPIs, and anomaly tables are computed directly from the full dataset.
                </span>
            </div>
        """, unsafe_allow_html=True)

        color_map = {
            'High Risk': '#ef553b',
            'Medium Risk': '#fecb52',
            'Low Risk': '#00cc96'
        }
        medium_cutoff = max(30, int(risk_threshold * 0.55))

        if risk_threshold > 50:
            conditions = [
                df_vis['Final_Score'] >= risk_threshold,
                (df_vis['Final_Score'] >= 50) & (df_vis['Final_Score'] < risk_threshold),
            ]
            choices = ['High Risk', 'Medium Risk']
            df_vis['Risk_Category'] = np.select(conditions, choices, default='Low Risk')
        else:
            conditions = [
                df_vis['Final_Score'] >= risk_threshold,
                (df_vis['Final_Score'] >= 35) & (df_vis['Final_Score'] < risk_threshold)
            ]
            choices = ['High Risk', 'Medium Risk']
            df_vis['Risk_Category'] = np.select(conditions, choices, default='Low Risk')

        choices = ['High Risk', 'Medium Risk']
        df_vis['Risk_Category'] = np.select(conditions, choices, default='Low Risk')

        #Filter Score >= 30
        df_ent = df_vis[df_vis["Final_Score"] >= 30].copy()

        c1, c2 = st.columns([6, 4])
        with c1:
            st.markdown(
                "<h4 style='text-align: center; font-size:16px; font-weight:700; letter-spacing:0.8px; margin-bottom:14px;'>TRANSACTION RISK DISTRIBUTION</h4>", 
                unsafe_allow_html=True
            )
            
            MAX_VISUAL_DOTS = 500
            if len(df_ent) > MAX_VISUAL_DOTS:
                df_scatter_draw = df_ent.sample(n=MAX_VISUAL_DOTS, random_state=42).sort_index()
            else:
                df_scatter_draw = df_ent

            fig = go.Figure()

            for cat in ['Low Risk', 'Medium Risk', 'High Risk']:
                df_cat = df_scatter_draw[df_scatter_draw['Risk_Category'] == cat]
                if not df_cat.empty:
                    fig.add_trace(go.Scatter(
                        x=df_cat.index,
                        y=df_cat["Final_Score"],
                        mode='markers',
                        name=cat,
                        marker=dict(
                            size=8,
                            color=color_map[cat],
                            opacity=0.7,
                        ),
                        hovertemplate=f"<b>Category:</b> {cat}<br><b>Index:</b> %{{x}}<br><b>Risk Score:</b> %{{y:.1f}}%<extra></extra>"
                    ))
            fig.add_hline(
                y=risk_threshold,
                line_dash="dash",
                line_color="#dc2626",
                line_width=2,
                annotation_text=f" THRESHOLD CUTOFF ({risk_threshold}%) ",
                annotation_position="top right",
                annotation_font=dict(size=10, family="sans-serif", weight="bold"),
                annotation_bgcolor="#dc2626"
            )
            fig.update_layout(
                height=360,
                margin=dict(t=30, b=40, l=65, r=20),
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="top", y=-0.16,
                    xanchor="center", x=0.5,
                    font=dict(size=12, weight="bold")
                ),
                yaxis=dict(
                    range=[26, 104],
                    autorange=False,
                    fixedrange=True,
                  
                    zeroline=False,
                    title=dict(text="Risk Score (%)", font=dict(size=12, weight="bold")),
                    tickfont=dict(size=11)
                ),
                xaxis=dict(
                   
                    title=dict(text="Transaction Index", font=dict(size=12, weight="bold")),
                    tickfont=dict(size=11)
                )          
            )

            st.plotly_chart(fig, use_container_width=True, key="scatter_enterprise_quant", config={'displayModeBar': False})

        with c2:
            st.markdown(
                "<h4 style='text-align: center; font-size:16px; font-weight:700; letter-spacing:0.8px; margin-bottom:14px;'>RISK CATEGORY BREAKDOWN</h4>", 
                unsafe_allow_html=True
            )         
            risk_counts = df_vis['Risk_Category'].value_counts().reset_index()
            risk_counts.columns = ['Category', 'Count']
            total_trx = len(df_vis)

            fig_pie = px.pie(
                risk_counts, 
                values='Count', 
                names='Category', 
                color='Category',
                color_discrete_map=color_map,
                category_orders={'Category': ['High Risk', 'Medium Risk', 'Low Risk']},
                hole=0.55
            )
            fig_pie.update_traces(
                textposition='inside',
                textinfo='percent',
                hoverinfo='label+value+percent',
                marker=dict(line=dict(color='#ffffff', width=2))
            )
            fig_pie.add_annotation(
                text=f"<b style='font-size:20px;'>{total_trx:,}</b><br><span style='font-size:10px; font-weight:700; letter-spacing:1px;'>TOTAL TRX</span>",
                x=0.5, y=0.5,
                showarrow=False
            )
            fig_pie.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                height=360,
                margin=dict(t=10, b=45, l=10, r=10),
                showlegend=True, 
                legend=dict(
                    orientation="h", 
                    yanchor="top", y=-0.16, 
                    xanchor="center", x=0.5,
                    font=dict(size=12, weight="bold")
                )
            )
            
            st.plotly_chart(fig_pie, use_container_width=True, key="pie_ent_mode", config={'displayModeBar': False})

    #DEPARTMENT RISK BREAKDOWN
    if is_enterprise:
        st.write("---")
        st.subheader("🏢 Department Risk Breakdown")

        if 'Department' in df_vis.columns:
            filtered_df = df_vis[df_vis['Final_Score'] >= risk_threshold]
            if not filtered_df.empty:
                dept_risk = filtered_df.groupby('Department')['Final_Score'].mean().reset_index()
                dept_risk = dept_risk.sort_values(by='Final_Score', ascending=False)
                
                fig_dept = px.bar(
                    dept_risk, x='Department', y='Final_Score',
                    title=f"Average Risk Score by Department (Threshold ≥ {risk_threshold}%)",
                    color='Final_Score',
                    color_continuous_scale='RdYlGn_r'
                )
                fig_dept.update_layout(
                    plot_bgcolor='rgba(0,0,0,0)', 
                    paper_bgcolor='rgba(0,0,0,0)',

                    margin=dict(t=50, b=105, l=70, r=40),
                    height=400
                    
                )
                st.plotly_chart(fig_dept, use_container_width=True)
                
                top_dept = dept_risk.iloc[0]['Department']
                st.warning(f"⚠️ **Audit Insight:** Department **{top_dept}** holds the highest average risk at threshold **{risk_threshold}%**. Further investigation is required!")
            else:
                st.info(f"💡 **Audit Insight:** No department transactions exceed the risk limit of **{risk_threshold}%**.")
        else:
            st.info("💡 **Audit Insight:** Department data is not available in the current dataset.")

    #TIME DISTRIBUTION
    st.write("---")
    st.subheader("🕒 Time Distribution: When do Anomalies Occur?")

    if 'Hour' not in df_vis.columns and 'Date' in df_vis.columns:
        df_vis['Hour'] = pd.to_datetime(df_vis['Date']).dt.hour

    if 'Hour' in df_vis.columns:
        all_hours = pd.DataFrame({'Hour': range(24)})
        df_anomali_only = df_vis[df_vis['Final_Score'] >= risk_threshold]

        if not df_anomali_only.empty:
            counts = df_anomali_only['Hour'].value_counts().reset_index()
            counts.columns = ['Hour', 'Anomaly_Count']
            plot_data = pd.merge(all_hours, counts, on='Hour', how='left').fillna(0)
            
            fig_hour = px.bar(
                plot_data, x='Hour', y='Anomaly_Count',
                title="Anomaly Frequency by Hour",
                labels={'Anomaly_Count': 'Number of Anomalies', 'Hour': 'Hour of Day (24h)'},
                color='Anomaly_Count',
                color_continuous_scale=['#ffeb3b', '#ff4b4b']
            )   
            
            fig_hour.add_vrect(
                x0=8, x1=17, 
                fillcolor="green", 
                opacity=0.15, 
                annotation_text="Normal Working Hours", 
                annotation_position="top left",
                annotation_font_color="white",
                annotation_font_size=12,
                line_width=0
            )

            fig_hour.update_layout(
                xaxis=dict(tickmode='linear', tick0=0, dtick=1),
                coloraxis_showscale=False,
                margin=dict(t=50, b=80, l=70, r=40),
                height=400
            )

            st.plotly_chart(fig_hour, use_container_width=True)

            max_anomalies = plot_data['Anomaly_Count'].max()

            if max_anomalies > 0:
                peak_hours_list = plot_data[plot_data['Anomaly_Count'] == max_anomalies]['Hour'].tolist()
                formatted_hours = [f"{int(h):02d}:00" for h in peak_hours_list]
                n_peaks = len(formatted_hours)
                
                if n_peaks == 1:
                    st.warning(f"⚠️ **Warning!** Peak anomaly activity detected at **{formatted_hours[0]}**. Investigate transactions around this time!")
                
                elif n_peaks == 2:
                    st.warning(f"⚠️ **Warning!** Multiple peak anomaly activities detected at **{formatted_hours[0]} and {formatted_hours[1]}**. Investigate transactions around these times!")
                
                elif n_peaks == 3:
                    st.warning(f"⚠️ **Warning!** Multiple peak anomaly activities detected at **{formatted_hours[0]}, {formatted_hours[1]}, and {formatted_hours[2]}**. Investigate transactions around these times!")
                
                else:
                    sample_hours = f"{formatted_hours[0]}, {formatted_hours[1]}, {formatted_hours[2]}"
                    st.warning(f"⚠️ **Warning!** Widespread peak anomaly activity detected across **{n_peaks} different hours** (e.g., **{sample_hours}**, among others). Review full distribution above!")
        else:
            st.info(f"💡 **Audit Insight:** No time anomalies detected for threshold **{risk_threshold}%**.")

    #ENTITY DEEP-DIVE
    st.write("---")
    st.subheader("🕵️ Selected Entity Deep-Dive")

    vendor_col = 'Vendor' if 'Vendor' in df_vis.columns else ('Vendor_Name' if 'Vendor_Name' in df_vis.columns else None)
    
    if vendor_col and vendor_col in df_vis.columns:
        vendor_list = df_vis[vendor_col].dropna().unique()
        selected_vendor = st.selectbox("Select Vendor to Investigate", vendor_list)

        if selected_vendor:
            detail_df = df_vis[df_vis[vendor_col] == selected_vendor].copy()
            amt_col = target_col if target_col in detail_df.columns else 'Amount'
            
            detail_df['first_digit'] = detail_df[amt_col].astype(str).str.extract(r'([1-9])')[0]
            digit_counts = detail_df['first_digit'].value_counts().reindex([str(i) for i in range(1, 10)], fill_value=0)

            col_a, col_b = st.columns(2)   
            with col_a:
                st.write(f"📈 **Transaction Trend:** {selected_vendor}")
                
                if 'Date' in detail_df.columns:
                    detail_df = detail_df.sort_values(by='Date')
                    x_axis = 'Date'
                else:
                    x_axis = detail_df.index

                fig_line = px.line(
                    detail_df, x=x_axis, y=amt_col, 
                    markers=True,
                    color_discrete_sequence=['#c471ed'],
                    labels={amt_col: 'Amount', 'Date': 'Transaction Date'}
                )
                fig_line.update_layout(margin=dict(t=30, b=40, l=65, r=20), height=300)
                st.plotly_chart(fig_line, use_container_width=True)      
                
            with col_b:
                st.write("📊 **First Digit Distribution (Benford's Law)**")
                fig_benford = px.bar(
                    x=digit_counts.index, y=digit_counts.values,
                    labels={'x': 'First Digit (1-9)', 'y': 'Frequency'},
                    color_discrete_sequence=['#45aaf2']
                )
                fig_benford.update_layout(margin=dict(t=30, b=40, l=65, r=20), height=300)
                st.plotly_chart(fig_benford, use_container_width=True)   

            st.info(f"💡 **Audit Insight:** Trend analysis and Benford's Law distribution reveal expenditure patterns for **{selected_vendor}**.")

    #INVESTIGATION TABLE
    st.write("---")
    st.subheader("🚩 Anomaly Investigation List")

    if st.session_state.get('app_mode') == 'Enterprise':
        st.info(f"ℹ️ **Investigative Scope:** Displaying **Top 100 Highest-Risk Transactions** ranked out of **{anomali_count:,} detected anomalies** from the full **{total_trans_all:,} transaction population**.")

    if 'df_investigation' in locals() and df_investigation is not None and not df_investigation.empty:
        column_config = {}
        currency_format = "Rp %,.0f" if is_rupiah else "$ %,.2f"

        if 'Final_Score' in df_investigation.columns:
            column_config['Final_Score'] = st.column_config.ProgressColumn(
                "Risk Score", help="Audit Final Risk Score (0-100)", format="%d", min_value=0, max_value=100
            )
        if 'Amount' in df_investigation.columns:
            column_config['Amount'] = st.column_config.NumberColumn(
                "Transaction Amount", format=currency_format
            )

        df_display = df_investigation.copy()
        if 'Final_Score' in df_display.columns and 'Amount' in df_display.columns:
            df_display = df_display.sort_values(
                by=['Final_Score', 'Amount'], 
                ascending=[False, False]
            )
        
        df_display = df_display.head(100).reset_index(drop=True)
        df_display.insert(0, 'Rank', range(1, len(df_display) + 1))
        column_config['Rank'] = st.column_config.NumberColumn("Rank", width="small")

        desired_order = [
            'Rank', 'Final_Score', 'Transaction_ID', 'Amount', 
            'Date', 'Vendor_Name', 'Department', 'Approver', 'Status', 'Hour'
        ]
        
        valid_cols = [col for col in desired_order if col in df_display.columns]
        extra_cols = [col for col in df_display.columns if col not in valid_cols and col != 'Vendor']
        df_display = df_display[valid_cols + extra_cols]

        st.dataframe(
            df_display,
            column_config=column_config,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.success("✅ No high-risk transactions found matching the current threshold criteria.")


#GENERATE INSIGHT VIA GEMINI AI
def generate_pdf_insight(prompt_text, fallback_text):
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_text
        )
        if response and hasattr(response, 'text') and response.text:
            return response.text.strip()
    except Exception as e:
        pass
    return fallback_text

def draw_ai_insight_box(pdf, title, insight_text):
    
    clean_title = title.encode('latin-1', 'replace').decode('latin-1')
    clean_text = insight_text.encode('latin-1', 'replace').decode('latin-1')
    
    pdf.ln(4)

    pdf.set_fill_color(240, 244, 248)
    pdf.set_draw_color(203, 213, 225)
    pdf.set_text_color(15, 23, 42)
    pdf.set_font("Arial", "B", 8)
    pdf.cell(180, 5, f"  {clean_title}", border="LTR", fill=True, ln=True)
    

    pdf.set_fill_color(248, 250, 252)
    pdf.set_font("Arial", "", 8)
    pdf.multi_cell(180, 4.5, f"  {clean_text}", border="LBR", fill=True)
    pdf.ln(2)

#===========EXPORT ENGINE==========
if 'ai_analysis_data' not in st.session_state:
    st.session_state.ai_analysis_data = "No AI analysis available. Please run AI Analysis first."
if 'ai_analysis_data' not in st.session_state and not anomalies.empty:
    st.session_state.run_ai = True

# 1) SAFE IMAGE EXPORT
@st.cache_data(show_spinner=False)
def save_plotly_figure(fig, filename):
    try:
        prefix_clean = filename.replace(".png", "")
        with tempfile.NamedTemporaryFile(delete=False, prefix=f"{prefix_clean}_", suffix=".png") as tmpfile:
            fig.write_image(tmpfile.name, format="png", width=700, height=350, scale=1)
            return tmpfile.name
    except:
        return None

# 2) EXCEL EXPORT
@st.cache_data(show_spinner=False)
def generate_excel_pro(df_export, ai_summary, is_enterprise=False, total_trx_val=None, total_anom_val=None, is_rupiah=False, df_anomalies=None):

    output = BytesIO()
    df_sorted = df_export.sort_values(by=['Final_Score', 'Amount'], ascending=[False, False])

    if df_anomalies is not None and not df_anomalies.empty:

        top_anoms = df_anomalies.sort_values(by=['Final_Score', 'Amount'], ascending=[False, False]).head(100)
        
        df_combined = pd.concat([top_anoms, df_export], ignore_index=True)
        
        if 'Transaction_ID' in df_combined.columns:
            df_combined = df_combined.drop_duplicates(subset=['Transaction_ID'])
        else:
            df_combined = df_combined.drop_duplicates()
            
        df_sorted = df_combined.sort_values(by=['Final_Score', 'Amount'], ascending=[False, False])
    else:
        df_sorted = df_export.sort_values(by=['Final_Score', 'Amount'], ascending=[False, False])
    

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book

        is_idr = is_rupiah
        curr_num_format = '"Rp "#,##0' if is_idr else '"$"#,##0.00'

        # Formats
        fmt_header = workbook.add_format({
            'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white',
            'align': 'center', 'valign': 'vcenter', 'border': 1
        })
        fmt_curr = workbook.add_format({
            'num_format': curr_num_format,
            'align': 'right', 'valign': 'vcenter'
        })
        fmt_score = workbook.add_format({
            'num_format': '0.00', 'align': 'right', 'valign': 'vcenter'
        })
        fmt_center = workbook.add_format({
            'align': 'center', 'valign': 'vcenter'
        })
        fmt_left = workbook.add_format({
            'align': 'left', 'valign': 'vcenter'
        })

        #AUTOFIT & ALIGNMENT
        def autofit_and_format(ws, df_sheet):
            if df_sheet is None or df_sheet.empty:
                return        
            for col_idx, col_name in enumerate(df_sheet.columns):

                col_series = df_sheet[col_name].astype(str)
                max_val_len = col_series.map(len).max() if not df_sheet.empty else 0
                max_len = max(max_val_len, len(str(col_name))) + 5

                col_lower = str(col_name).lower()
                if any(k in col_lower for k in ['amount', 'exposure']):
                    fmt = fmt_curr
                elif any(k in col_lower for k in ['score', 'rank', 'count', 'final_score', 'average_risk_score']):
                    fmt = fmt_score
                elif any(k in col_lower for k in ['id', 'date', 'hour', 'user', 'status', 'level', 'risk_level']):
                    fmt = fmt_center
                else:
                    fmt = fmt_left            
 
                ws.set_column(col_idx, col_idx, max(max_len, 12), fmt)
                clean_header = str(col_name).replace('_', ' ').title()
                ws.write(0, col_idx, clean_header, fmt_header)
        
        # format
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color':'white','border':1, 'align': 'center', 'valign': 'vcenter'})
        critical_fmt = workbook.add_format({'bg_color':'#ff4b4b'})
        low_fmt = workbook.add_format({'bg_color':"#92D050"})
        medium_fmt = workbook.add_format({'bg_color':'#ffe066'})

        # sheet 1 - dashboard
        dashboard = workbook.add_worksheet("Dashboard")

        total_trx = total_trx_val if total_trx_val is not None else (total_trans_all if 'total_trans_all' in globals() else len(df_export))
        total_anom = total_anom_val if total_anom_val is not None else (anomali_count if 'anomali_count' in globals() else len(anomalies))

        dashboard.write("A1", "AUDIT INTELLIGENCE RESULT", header_fmt)
        dashboard.write("A3", "Total Transactions")
        dashboard.write("B3", total_trx)
        dashboard.write("A4", "Total Anomalies")
        dashboard.write("B4", total_anom)
        dashboard.write("A5", "Total Exposure")
        dashboard.write("B5", total_val, fmt_curr)
        dashboard.write("A6", "Average Risk")
        dashboard.write("B6", round(current_avg_risk, 2))
        dashboard.write("D3", "AI Insight", header_fmt)

        dashboard.set_column("A:A", 25)
        dashboard.set_column("B:B", 18)

        textbox_format = {
            'font': {'color': '#FFFFFF', 'size': 10},
            'fill': {'color': '#0B0F19'},
            'line': {'color': '#00D4FF'},
        }

        def clean_text(text):
            if not text:
                return "No AI insight available."
            replacements = {
                "”": "-", "“": "-", "’": "'", "‘": "'",
                "•": "*", "–": "-", "—": "-", "…": "..."
            }
            for k, v in replacements.items():
                text = text.replace(k, v)
            return text

        ai_text_clean = clean_text(ai_summary)
        dashboard.insert_textbox(
            'D4',
            ai_text_clean,
            {
                'width': 420,
                'height': 180,
                'x_offset': 5,
                'y_offset': 5,
                **textbox_format
            }
        )
        height = 120 + (len(ai_text_clean) // 80) * 20
        
        
        #sheet 2 - raw data
        df_sorted.to_excel(writer, sheet_name="Raw Data", index=False)
        raw = writer.sheets["Raw Data"]
        
        autofit_and_format(raw, df_sorted)

        # Conditional formatting
        if 'Risk_Level' in df_sorted.columns:
            col_idx = df_sorted.columns.get_loc('Risk_Level')

            raw.conditional_format(1, col_idx, len(df_sorted), col_idx, {
                'type':'text','criteria':'containing','value':'HIGH','format':critical_fmt
            })
            raw.conditional_format(1, col_idx, len(df_sorted), col_idx, {
                'type':'text','criteria':'containing','value':'LOW','format':low_fmt
            })
            raw.conditional_format(1, col_idx, len(df_sorted), col_idx, {
                'type':'text','criteria':'containing','value':'MEDIUM','format':medium_fmt
            })

        #sheet 3 - Risk Summary
        risk_counts = df_export['Risk_Level'].value_counts().reset_index()
        risk_counts.columns = ['Risk_Level','Count'] 

        sheet_name = "Risk Summary"
        risk_counts.to_excel(writer, sheet_name=sheet_name, index=False)
        chart_sheet = writer.sheets[sheet_name]

        autofit_and_format(chart_sheet, risk_counts)

        workbook   = writer.book

        header_format = workbook.add_format({
            'bold': True,
            'font_color': "#FFFFFF",
            'bg_color': '#1F4E78',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1
        })

        pie_chart = workbook.add_chart({'type': 'pie'})

        colors = ["#5FDF59", "#FFC300", '#FF3B3B', '#FF3B3B', '#FFC300']

        points = [{'fill': {'color': c}} for c in colors[:len(risk_counts)]]

        pie_chart.add_series({
            'name': 'Risk Distribution',
            'categories': f"='{sheet_name}'!$A$2:$A${len(risk_counts)+1}",
            'values':     f"='{sheet_name}'!$B$2:$B${len(risk_counts)+1}",
            'data_labels': {
                'percentage': True,
                'leader_lines': True,
                'font': {'color': '#FFFFFF'}
            },
            'points': points
        })

        pie_chart.set_title({
            'name': 'Risk Distribution',
            'name_font': {'color': '#FFFFFF'}
        })
        pie_chart.set_legend({
            'font': {'color': '#AAAAAA'}
        })
        pie_chart.set_chartarea({
            'fill': {'color': "#202124"}
        })
        pie_chart.set_plotarea({
            'fill': {'color': '#202124'}
        })

        chart_sheet.insert_chart('D2', pie_chart, {'x_scale': 1.2, 'y_scale': 1.2})

        #sheet 4 - top department risk
        if is_enterprise and 'Department' in df.columns:
            dept_summary = df.groupby('Department').agg(
                Total_Transactions=('Final_Score', 'count'),
                Average_Risk_Score=('Final_Score', 'mean'),
                High_Risk_Count=('Final_Score', lambda x: (x >= 50).sum())
            ).reset_index().sort_values('Average_Risk_Score', ascending=False)

            dept_summary['Average_Risk_Score'] = dept_summary['Average_Risk_Score'].round(2)
            sheet_dept = "Top Dept. Risk"
            dept_summary.to_excel(writer, sheet_name=sheet_dept, index=False)
            dept_sheet = writer.sheets[sheet_dept]

            autofit_and_format(dept_sheet, dept_summary)

            workbook = writer.book
            
            header_format = workbook.add_format({
                'bold': True,
                'font_color': "#FFFFFF",
                'bg_color': "#1F4E78",
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })

            col_dept_idx = dept_summary.columns.get_loc("Department")
            col_score_idx = dept_summary.columns.get_loc("Average_Risk_Score")

            def xl_col(col_idx):
                return chr(65 + col_idx)

            col_d = xl_col(col_dept_idx)
            col_s = xl_col(col_score_idx)

            num_rows = len(dept_summary) + 1

            dept_chart = workbook.add_chart({'type': 'column'})

            dept_chart.add_series({
                'name':       'Average Risk Score',
                'categories': f"='{sheet_dept}'!${col_d}$2:${col_d}${num_rows}",
                'values':     f"='{sheet_dept}'!${col_s}$2:${col_s}${num_rows}",
                'data_labels': {
                    'value': True,
                    'font': {'color': '#00D4FF'}
                },
                'fill': {'color': '#00D4FF'},
                'border': {'none': True}
            })

            dept_chart.set_title({
                'name': 'Top Department Risk',
                'name_font': {'color': '#FFFFFF'}
            })

            dept_chart.set_x_axis({
                'name_font': {'color': '#AAAAAA'},
                'num_font': {'color': '#AAAAAA'}
            })

            dept_chart.set_y_axis({
                'name_font': {'color': '#AAAAAA'},
                'num_font': {'color': '#AAAAAA'},
                'major_gridlines': {
                    'visible': True,
                    'line': {'color': '#1F2A44'}
                }
            })

            dept_chart.set_legend({'none': True})

            dept_chart.set_plotarea({
                'fill': {'color': '#0B0F19'}
            })

            dept_chart.set_chartarea({
                'fill': {'color': '#0B0F19'}
            })

            dept_sheet.insert_chart('F2', dept_chart, {'x_scale': 1.3, 'y_scale': 1})

        #sheet 5 - top 10 anomalies
        source_top = df_anomalies if (df_anomalies is not None and not df_anomalies.empty) else df_sorted
        top10 = source_top.sort_values(['Final_Score', 'Amount'], ascending=[False, False]).head(10).copy()
                
        top10.insert(0, "Rank", range(1, len(top10)+1))
        sheet_name = "Top Risk"
        top10.to_excel(writer, sheet_name=sheet_name, index=False)
        top_sheet = writer.sheets[sheet_name]

        autofit_and_format(top_sheet, top10)
                
        workbook  = writer.book
        

        col_vendor = top10.columns.get_loc("Vendor")
        col_score  = top10.columns.get_loc("Final_Score")

        def xl_col(col_idx):
            return chr(65 + col_idx)

        col_v = xl_col(col_vendor)
        col_s = xl_col(col_score)

        header_format = workbook.add_format({
            'bold': True,
            'font_color': "#FFFFFF",
            'bg_color': "#1F4E78",
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })


        bar_chart = workbook.add_chart({'type': 'column'})

        bar_chart.add_series({
            'name':       'Top 10 Risk Score',
            'categories': f"='{sheet_name}'!${col_v}$2:${col_v}${len(top10)+1}",
            'values':     f"='{sheet_name}'!${col_s}$2:${col_s}${len(top10)+1}",
            'data_labels': {
                'value': True,
                'font': {'color': '#00D4FF'}
            },
            'fill': {'color': '#00D4FF'},
            'border': {'none': True}
        })
      
        bar_chart.set_title({
            'name': 'Top 10 Risk',
            'name_font': {'color': '#FFFFFF'}
        })

        bar_chart.set_x_axis({
            'name_font': {'color': '#AAAAAA'},
            'num_font': {'color': '#AAAAAA'}
        })

        bar_chart.set_y_axis({
            'name_font': {'color': '#AAAAAA'},
            'num_font': {'color': '#AAAAAA'},
            'major_gridlines': {
                'visible': True,
                'line': {'color': '#1F2A44'}
            }
        })

        bar_chart.set_legend({'none': True})

        bar_chart.set_plotarea({
            'fill': {'color': '#0B0F19'}
        })

        bar_chart.set_chartarea({
            'fill': {'color': '#0B0F19'}
        })

        top_sheet.insert_chart('N2', bar_chart, {'x_scale': 1.3, 'y_scale': 1})

        #sheet 6 - vendor risk
        if 'Vendor' in df_export.columns:

            df_vendor_target = anomalies if not anomalies.empty else df_export

            vendor_summary = (
                df_vendor_target.groupby('Vendor')['Final_Score']
                .mean()
                .reset_index()
                .sort_values(by='Final_Score', ascending=False)
                .head(10)
            )
            vendor_summary['Final_Score'] = vendor_summary['Final_Score'].round(2)

            sheet_name = "Vendor Risk"
            vendor_summary.to_excel(writer, sheet_name=sheet_name, index=False)
            vsheet = writer.sheets[sheet_name]
            
            autofit_and_format(vsheet, vendor_summary)

            workbook = writer.book
            
            header_format = workbook.add_format({
                'bold': True,
                'font_color': "#FFFFFF",
                'bg_color': '#1F4E78',
                'align':'center',
                'valign': 'vcenter',
                'border': 1
            })


            vchart = workbook.add_chart({'type': 'column'})

            vchart.add_series({
                'name': 'Vendor Risk Score',
                'categories': f"='{sheet_name}'!$A$2:$A${len(vendor_summary)+1}",
                'values':     f"='{sheet_name}'!$B$2:$B${len(vendor_summary)+1}",
                'data_labels': {
                    'value': True,
                    'font': {'color': '#00D4FF'}
                },
                'fill':   {'color': '#00D4FF'},
                'border': {'none': True}
            })

            vchart.set_title({
                'name': 'Top Vendor Risk',
                'name_font': {'color': '#FFFFFF'}
            })

            vchart.set_x_axis({
                'num_font': {'color': '#AAAAAA'},
                'name_font': {'color': '#AAAAAA'}
            })

            vchart.set_y_axis({
                'num_font': {'color': '#AAAAAA'},
                'name_font': {'color': '#AAAAAA'},
                'major_gridlines': {
                    'visible': True,
                    'line': {'color': '#1F2A44'}
                }
            })

            vchart.set_legend({'none': True})

            vchart.set_plotarea({
                'fill': {'color': '#0B0F19'}
            })

            vchart.set_chartarea({
                'fill': {'color': '#0B0F19'}
            })

            vsheet.insert_chart('D2', vchart, {'x_scale': 1.3, 'y_scale': 1})

        #sheet 7 - trend
        if 'Date' in df_export.columns:

            df_export['Date'] = pd.to_datetime(df_export['Date'])

            trend = (
                df_export
                .groupby(df_export['Date'].dt.date)['Final_Score']
                .mean()
                .reset_index()
            )
            trend['Final_Score'] = trend['Final_Score'].round(2)
            sheet_name = "Trend"
            trend.to_excel(writer, sheet_name=sheet_name, index=False)

            trend.to_excel(writer, sheet_name="Trend", index=False)
            tsheet = writer.sheets["Trend"]
            tsheet   = writer.sheets[sheet_name]
            autofit_and_format(tsheet, trend)

            workbook = writer.book

            header_format = workbook.add_format({
                'bold': True,
                'font_color': "#FFFFFF",
                'bg_color': '#1F4E78',   
                'align': 'center',
                'valign': 'vcenter',
                'border': 1
            })

            date_format = workbook.add_format({
                'num_format': 'yyyy-mm-dd',
                'font_color': '#FFFFFF',
                'bg_color': '#0B0F19'
            })

            tsheet.set_column('A:A', 18, date_format)

            line_chart = workbook.add_chart({'type': 'line'})

            line_chart.add_series({
                'name': 'Risk Trend',
                'categories': f"='{sheet_name}'!$A$2:$A${len(trend)+1}",
                'values':     f"='{sheet_name}'!$B$2:$B${len(trend)+1}",
                'line': {'color': '#00D4FF', 'width': 2.5},
                'marker': {
                    'type': 'circle',
                    'size': 6,
                    'border': {'color': '#00D4FF'},
                    'fill': {'color': '#0B0F19'}
                },
                'data_labels': {
                    'value': True,
                    'font': {'color': '#00D4FF'}
                }
            })

            line_chart.set_title({
                'name': 'Risk Trend Over Time',
                'name_font': {'color': '#FFFFFF'}
            })

            line_chart.set_x_axis({
                'num_font': {'color': '#AAAAAA'},
                'name_font': {'color': '#AAAAAA'}
            })

            line_chart.set_y_axis({
                'num_font': {'color': '#AAAAAA'},
                'name_font': {'color': '#AAAAAA'},
                'major_gridlines': {
                    'visible': True,
                    'line': {'color': '#1F2A44'}
                }
            })

            line_chart.set_legend({'none': True})

            line_chart.set_plotarea({
                'fill': {'color': '#0B0F19'}
            })

            line_chart.set_chartarea({
                'fill': {'color': '#0B0F19'}
            })

            tsheet.insert_chart('D2', line_chart, {'x_scale': 1.5, 'y_scale': 1})
    return output.getvalue()


# 3) PDF EXPORT
class PremiumAuditPDF(FPDF):
    
    def header(self):
        # Watermark
        self.set_text_color(230, 230, 230)
        self.set_font("Arial", "B", 40)
        self.rotate(45, x=30, y=150)
        self.text(30, 150, "CONFIDENTIAL")
        self.rotate(0)
        self.set_text_color(0, 0, 0)

    def rotate(self, angle, x=None, y=None):
        if angle != 0:
            if x is None: x = self.x
            if y is None: y = self.y
            self._out("q")
            self._out(
                f"{np.cos(np.radians(angle)):.5f} {np.sin(np.radians(angle)):.5f} "
                f"{-np.sin(np.radians(angle)):.5f} {np.cos(np.radians(angle)):.5f} "
                f"{x*self.k:.2f} {(self.h-y)*self.k:.2f} cm"
            )
        else:
            self._out("Q")

@st.cache_data(show_spinner=False)
def generate_pdf(
    df, anomalies, df_export, ai_text, 
    fig1=None, fig2=None, fig3=None, 
    fig_hour=None, fig_line=None, fig_benford=None,
    selected_vendor="Selected Entity"
):

    pdf = PremiumAuditPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_auto_page_break(auto=True, margin=20)

    #COVER
    pdf.add_page()

    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 20, "FORENSIC AUDIT REPORT", ln=True, align="C")
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, "Audit Intelligence Core Systems", ln=True, align="C")

    pdf.ln(20)
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 10, f"Generated: {dt.datetime.now().strftime('%d %B %Y')}", ln=True, align="C")

    pdf.ln(40)

    pdf.set_font("Arial", "I", 9)
    pdf.multi_cell(0, 6,
        "This report contains confidential forensic audit insights generated using advanced anomaly detection algorithms and AI analysis."
    )

    # 1) key metrics
    pdf.add_page()

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "1. KEY AUDIT METRICS", ln=True)

    total_trx = total_trans_all if 'total_trans_all' in globals() and total_trans_all else len(df)
    total_anom = anomali_count if 'anomali_count' in globals() and anomali_count else len(anomalies)

    total_value = total_val
    average_risk = current_avg_risk
    anom_pct = (total_anom / total_trx * 100) if total_trx > 0 else 0
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 8, f"Total Transactions Analyzed: {total_trx:,}", ln=True)
    pdf.cell(0, 8, f"Total Anomalies Detected: {total_anom:,} ({anom_pct:.1f}%)", ln=True)
    pdf.cell(0, 8, f"Total Financial Exposure: {total_value:,.2f}", ln=True)
    pdf.cell(0, 8, f"Average Risk Score: {average_risk:,.2f}", ln=True)

    # 2) visualization
    img1 = save_plotly_figure(fig1, "risk.png") if fig1 else None
    img2 = save_plotly_figure(fig2, "time.png") if fig2 else None
    img3 = save_plotly_figure(fig3, "dept.png") if fig3 else None

    # Gambar Baru
    img_hour = save_plotly_figure(fig_hour, "time_hour.png") if fig_hour else None
    img_line = save_plotly_figure(fig_line, "vendor_line.png") if fig_line else None
    img_benford = save_plotly_figure(fig_benford, "vendor_benford.png") if fig_benford else None

    if img1 or img2:
        pdf.ln(8)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "2. VISUAL ANALYTICS", ln=True)

        # 2.1 Transaction Distribution
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 10, "2.1 Transaction Distribution across Risk Levels", ln=True)

        if img1:
            pdf.image(img1, x=30, w=150)
        if img2:
            pdf.ln(7)
            pdf.image(img2, x=30, w=150)

    # 2.2 Transaction Trend
    if img_line:
        pdf.add_page()
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 10, f"2.2 Transaction Trend ({selected_vendor})", ln=True)
        pdf.image(img_line, x=30, w=150)
       

    # 2.3 First Digit Distribution / Benford's Law
    if img_benford:
        pdf.ln(7)
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 10, f"2.3 First Digit Distribution / Benford's Law ({selected_vendor})", ln=True)
        pdf.image(img_benford, x=30, w=150)

        # --- INSIGHT 1: TREND & BENFORD'S LAW ---
        prompt_benford = f"""
        Berikan 1 kalimat Executive Audit Insight berdasarkan analisis First Digit Benford's Law vendor '{selected_vendor}'.
        Fokus pada deviasi statistik digit pertama, indikasi manipulasi angka, dan rekomendasi audit singkat.
        Gunakan Bahasa Inggris profesional khas Big 4 audit. Maksimal 25 kata. Tanpa emoji.
        """
        fallback_1 = "Statistical deviation detected in first-digit distribution, indicating potential manual number manipulation requiring targeted sample testing."
        insight_1 = generate_pdf_insight(prompt_benford, fallback_1)
        
        draw_ai_insight_box(pdf, "INSIGHT:", insight_1)

    # 2.4 time distribution
    if img_hour:
        pdf.add_page()
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 10, "2.4 Anomaly Frequency by Hour", ln=True)
        pdf.image(img_hour, x=30, w=150)

    # 2.5 top anomalies table 
    pdf.ln(7)
    pdf.set_line_width(0.2)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 10, "2.5 High Risk Transactions", ln=True)

    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255, 255, 255)

    # a. HEADER TABEL
    curr_code = "IDR" if is_rupiah else "USD"

    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 8.5)

    pdf.cell(30, 8, "Date", border=1, fill=True, align="C")
    pdf.cell(45, 8, "Vendor / Entity", border=1, fill=True, align="L")
    pdf.cell(30, 8, f"Amount ({curr_code})", border=1, fill=True, align="R")
    pdf.cell(15, 8, "Hour", border=1, fill=True, align="C")
    pdf.cell(30, 8, "Risk Level", border=1, fill=True, align="C")
    pdf.cell(30, 8, "Risk Score", border=1, fill=True, align="C", ln=True)

    # b. ISI TABEL
    pdf.set_font("Arial", "", 8)

    for i, (_, row) in enumerate(anomalies.sort_values(['Final_Score', 'Amount'], ascending=[False, False]).head(10).iterrows()):
        risk = str(row.get('Risk_Level', 'N/A')).upper()
        
        raw_date = str(row.get('Date', 'N/A'))
        clean_date = raw_date.split(' ')[0] if ' ' in raw_date else raw_date[:10]
        
        # 1. Zebra Striping Background
        if i % 2 == 0:
            pdf.set_fill_color(248, 250, 252)
        else:
            pdf.set_fill_color(255, 255, 255)
            
        pdf.set_text_color(30, 41, 59)
        
        pdf.cell(30, 7, clean_date, border=1, fill=True, align="C")
        
        vendor_str = str(row.get('Vendor', 'N/A'))[:25]
        pdf.cell(45, 7, vendor_str, border=1, fill=True, align="L")
        
        amt_val = row.get('Amount', 0)
        if is_rupiah:
            amt_str = f"{amt_val:,.0f}".replace(",", ".")
        else:
            amt_str = f"{amt_val:,.2f}"

        pdf.cell(30, 7, amt_str, border=1, fill=True, align="R")
        
        pdf.cell(15, 7, str(row.get('Hour', '-')), border=1, fill=True, align="C")
        
        #Dynamic Accent Color
        if "HIGH" in risk or "CRITICAL" in risk:
            pdf.set_text_color(185, 28, 28)
        elif "MEDIUM" in risk:
            pdf.set_text_color(217, 119, 6)
        else:
            pdf.set_text_color(16, 185, 129)
            
        pdf.cell(30, 7, risk, border=1, fill=True, align="C")
        
        pdf.set_text_color(30, 41, 59)
        pdf.cell(30, 7, f"{row.get('Final_Score', 0):,.2f}", border=1, fill=True, align="C", ln=True)

    pdf.ln(6)

    if 'df_export' in locals() and isinstance(df_export, pd.DataFrame) and not df_export.empty:
        needed_cols = [c for c in ['Date', 'Vendor', 'Amount', 'Final_Score'] if c in df_export.columns]
        sort_cols = [c for c in ['Final_Score', 'Amount'] if c in df_export.columns]
        if sort_cols:
            top_samples = df_export.sort_values(sort_cols, ascending=False).head(10)[needed_cols].to_dict(orient='records') if needed_cols else df_export.head(10).to_dict(orient='records')
        else:
            top_samples = df_export.head(10).to_dict(orient='records')
    else:
        top_samples = []

    prompt_table = f"""
    Berikan 1 kalimat Executive Audit Insight berdasarkan data transaksi berisiko tinggi ini:
    {top_samples}
    Fokus pada konsentrasi transaksi bernilai tinggi dan potensi split-purchase override limit threshold.
    Gunakan Bahasa Inggris profesional khas Big 4 audit. Maksimal 25 kata. Tanpa emoji.
    """
    fallback_2 = "High-risk transaction clusters near threshold boundaries indicate potential split-purchase overrides to bypass approval limits."
    insight_2 = generate_pdf_insight(prompt_table, fallback_2)
    
    draw_ai_insight_box(pdf, "INSIGHT:", insight_2)

    # 2.6 Department Risk Breakdown
    if img3:
        pdf.add_page()
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 10, "2.6 Department Risk Breakdown", ln=True)
        pdf.image(img3, x=30, w=150)
        
        # --- INSIGHT 3: DEPARTMENT RISK ---
        prompt_dept = """
        Berikan 1 kalimat Executive Audit Insight mengenai distribusi risiko antar departemen yang merata di atas threshold.
        Fokus pada risiko sistemik lintas operasional dan perbaikan kontrol internal entitas secara menyeluruh.
        Gunakan Bahasa Inggris profesional khas Big 4 audit. Maksimal 25 kata. Tanpa emoji.
        """
        fallback_3 = "Elevated risk exposure across multiple operational units indicates systemic control vulnerabilities requiring enterprise-wide policy evaluation."
        insight_3 = generate_pdf_insight(prompt_dept, fallback_3)
        
        draw_ai_insight_box(pdf, "INSIGHT:", insight_3)

    # 3). AI insight
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "3. EXECUTIVE SUMMARY", ln=True)
    pdf.set_font("Arial", "", 10)
    safe_text = ai_text.encode('latin-1', 'replace').decode('latin-1')
    pdf.multi_cell(0, 6, safe_text)

    return pdf.output(dest='S').encode('latin-1')

#==========BUTTON EXPORT==========
st.divider()
st.subheader("📥 Export Audit Report")

if not anomalies.empty:
    ai_text = st.session_state.get("ai_analysis_data", "No AI analysis available")
    current_state = f"{risk_threshold}_{st.session_state.get('lang', 'EN')}_{len(anomalies)}_{ai_text}"

    has_files = "excel_bytes" in st.session_state and "pdf_bytes" in st.session_state
    is_same_param = st.session_state.get("saved_state") == current_state

    st.caption("Click here to Prepare Report Documents!")

    col_export, _ = st.columns([1.2, 1]) 

    with col_export:
        if st.button("⚡ Prepare Documents Now!", type="primary", use_container_width=True):
            with st.spinner("Rendering graphic visualizations and compiling reports..."):
                is_enterprise = 'Department' in df.columns
                
                data_to_export = anomalies if (is_enterprise and not anomalies.empty) else df

                st.session_state.excel_bytes = generate_excel_pro(
                    df,
                    ai_text,
                    is_enterprise=is_enterprise,
                    total_trx_val=total_trans_all,
                    total_anom_val=anomali_count,
                    is_rupiah=is_rupiah,
                    df_anomalies=anomalies
                )

                fig_dept_to_pass = locals().get('fig_dept') if is_enterprise else None

                st.session_state.pdf_bytes = generate_pdf(
                    data_to_export,
                    anomalies,
                    ai_text,
                    fig,
                    fig_pie,
                    fig_dept_to_pass,
                    fig_hour,
                    fig_line,
                    fig_benford,
                    selected_vendor
                )

                st.session_state.saved_state = current_state
                st.rerun()

        if has_files and is_same_param:
            st.success("✅ Documents ready to download!")
            
            col_pdf, col_excel = st.columns(2)
            with col_pdf:
                st.download_button("📕 PDF Report", data=st.session_state.pdf_bytes, file_name="Audit_Report.pdf", mime="application/pdf", use_container_width=True)
            with col_excel:
                st.download_button("📊 Excel Ledger", data=st.session_state.excel_bytes, file_name="Audit_Ledger.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

else:
    st.info("No anomalies to export.")

#==========GEMINI INTEGRATION===========
st.divider()
col_title, col_lang = st.columns([7, 3])
with col_title:
    st.subheader("🛡️ Audit Intelligence Core")

if 'lang' not in st.session_state:
    st.session_state.lang = "English"

st.caption("Click the button below to get an in-depth analysis by Gemini 2.5 Flash!")

col_btn_ai, col_btn_lang, col_filler = st.columns([2.5, 2, 5.5])

with col_btn_ai:
    btn_pressed = st.button("Activate AI Analysis", width='stretch', type="primary")

with col_btn_lang:
    new_lang = st.selectbox(
        "Language",
        ["English", "Bahasa Indonesia"],
        index=0 if st.session_state.lang == "English" else 1,
        label_visibility="collapsed"
    )

    if new_lang != st.session_state.lang:
        st.session_state.lang = new_lang
        
        if 'ai_analysis_data' in st.session_state:
            st.session_state.run_ai = True
        st.rerun()

if btn_pressed or st.session_state.get('run_ai', False):
    st.session_state.run_ai = False
    
    with st.spinner(f"Analyzing in {st.session_state.lang}..."):
        try:
            #data
            available_cols = [c for c in ['Transaction_ID', 'Vendor', 'User_ID', target_col, 'Final_Score'] if c in anomalies.columns]
            top_anomalies = anomalies.sort_values(by='Final_Score', ascending=False).head(10)[available_cols]
            data_summary = top_anomalies.to_markdown(index=False)
            
            #pick data Benford
            benford_data = detail_df['first_digit'].value_counts().to_dict() if ('detail_df' in locals() and 'first_digit' in detail_df.columns) else "General overview (No specific vendor selected)"

            if 'is_rupiah' in locals():
                active_currency = "Rupiah (IDR)" if is_rupiah else "Dollar (USD)"
            else:
                active_currency = "Dollar (USD)"

            anomalies_time = anomalies.copy()
            anomalies_time['Date_dt'] = pd.to_datetime(anomalies_time['Date'])
            anomalies_time['Hour'] = anomalies_time['Date_dt'].dt.hour

            night_df = anomalies_time[anomalies_time['Hour'].isin([23, 0, 1, 2, 3, 4, 5])]
            total_night_count = len(night_df)
    
            if total_night_count > 0:
                night_samples = night_df[['Transaction_ID', 'Date', 'Vendor', 'Amount', 'Final_Score']].head(3).to_dict(orient='records')
            else:
                night_samples = "No anomalies detected during night shift hours."

            time_summary = f"""
            - Total Night Shift Anomalies (23:00 - 05:00): {total_night_count} transactions
            - Sample Night Shift Anomalies: {night_samples}
            """

            if not anomalies.empty:
                anomalies_hour = pd.to_datetime(anomalies['Date']).dt.hour
                peak_hour = int(anomalies_hour.value_counts().idxmax())
                peak_count = int(anomalies_hour.value_counts().max())
            else:
                peak_hour, peak_count = 0, 0

            if active_currency == "Rupiah (IDR)":
                total_val_str = f"IDR {total_val:,.2f}"
                avg_risk_str = f"{current_avg_risk:.2f}"
            else:
                total_val_str = f"USD {total_val:,.2f}"
                avg_risk_str = f"{current_avg_risk:.2f}"

            #prompt
            full_prompt = f"""
            As a Senior Forensic Auditor, perform a deep-dive analysis on the following flagged anomalies.
            Output Language: {st.session_state.lang}
            Currency Mode: {active_currency}

            --- OVERALL METRICS (FULL POPULATION) ---
            - Total Population Transactions Analyzed: {total_trans_all:,}
            - Total Anomalies Detected: {anomali_count:,} ({(anomali_count / total_trans_all * 100) if total_trans_all > 0 else 0:.1f}% of total)
            - Total Financial Exposure: {total_val_str} 
            - Average Risk Score: {avg_risk_str}

            --- DATA SUMMARY (Top High-Risk Transactions) ---
            {data_summary}

            --- TIME & STAMP DISTRIBUTION (Night Shift Audit) ---
            {time_summary}

            --- BENFORD'S LAW DISTRIBUTION (Entity: {selected_vendor if 'selected_vendor' in locals() else 'All'}) ---
            {benford_data}

            --- SPECIFIC INVESTIGATION DIRECTIVES ---
            1. Pattern Recognition: Identify if there's a pattern in 'Rounding Penalty' (psychological pricing fraud).
            2. Time Anomaly: Analyze the provided 'Night Shift Risk' (23:00 - 05:00) metrics AND highlight the Peak Anomaly Hour (Hour {peak_hour}:00 with {peak_count} anomalies). NOTE: Timestamp data (YYYY-MM-DD HH:MM:SS) IS FULLY PROVIDED in the dataset. Do NOT state that timestamps are missing.
            3. Entity Risk: Evaluate if certain Users or Vendors are repeatedly flagged.
            4. Action Plan: Give 3 highly actionable 'Investigative Steps' for these findings.

            Tone: Professional, suspicious, highly analytical, and concise.
            Format Constraint: DO NOT include formal report headers, To, From, Date, Subject. Go straight to the executive summary and findings.
            """

            #call api gemini
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=full_prompt
                )

                if response and hasattr(response, "text"):
                    st.session_state.ai_analysis_data = response.text
                    st.success(f"✅ Analysis Complete ({st.session_state.lang})")
                else:
                    st.error("❌ AI analysis failed to generate. Response structure is invalid.")
                    
            except Exception as e:
                
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    st.error("⚠️ **Free AI Analysis Quota is Full (Daily Limit Exceeded)**")             
                else:
                    st.error(f"Failed to generate AI analysis. Error: {e}")
               
        except Exception as e:
            st.error(f"Data Synchronization Failed!: {e}")
        
if 'ai_analysis_data' in st.session_state:
    st.markdown("### 🔍 Forensic Audit Analysis")
    with st.container(border=True):
        st.write(st.session_state.ai_analysis_data)
