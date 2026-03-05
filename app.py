import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="AUDIT INTELLIGENCE CORE SYSTEMS", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.9rem !important; }
    .stMetric { 
        background-color: #ffffff; 
        padding: 10px 15px !important; 
        border-radius: 10px; 
        box-shadow: 2px 2px 10px rgba(0,0,0,0.05);
    }
    </style>
    """, unsafe_allow_html=True)

# --- 2. SIDEBAR ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3201/3201521.png", width=100)
    st.title("Audit Control Panel")
    
    st.markdown(f"""
        <div style='background-color: #e1f5fe; padding: 15px; border-radius: 10px; border-left: 5px solid #0288d1;'>
            <p style='margin: 0; font-weight: bold; color: #01579b;'>Developed by:</p>
            <p style='margin: 0; font-size: 1.1em; font-weight: bold; color: #000;'>Uhuud Said</p>
            <hr style='margin: 10px 0;'>
            <p style='margin: 0; font-size: 0.85em; color: #0277bd;'><b>Quantitative Developer</b></p>
            <p style='margin: 0; font-size: 0.85em; color: #0277bd;'><b>Statistical Consultant</b></p>
        </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    st.subheader("1. Data Sample")
    currency_choice = st.radio("Choose Sample Currency:", ["Rupiah (IDR)", "Dollar (USD)"])

    @st.cache_data
    def generate_large_sample(mode):
        np.random.seed(42)
        low, high = (1000000, 100000000) if mode == "Rupiah (IDR)" else (100, 50000)
        user_list = [f"User-0{i}" for i in range(1, 7)]
        data_sample = {
            'Transaction_ID': [f"TRX-2025-{i:04d}" for i in range(1, 1501)],
            'Date': pd.date_range(start='2025-01-01', periods=1500, freq='H'),
            'User_ID': np.random.choice(user_list, 1500),
            'Vendor': np.random.choice(['Vendor A', 'Vendor B', 'Vendor C', 'Vendor D', 'Vendor E'], 1500),
            'Amount': np.random.uniform(low, high, 1500).round(2),
            'Description': np.random.choice(['Service Fee', 'Procurement', 'Maintenance', 'Operational'], 1500)
        }
        return pd.DataFrame(data_sample)

    def get_xlsx_sample(df_sample):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_sample.to_excel(writer, index=False, sheet_name='Audit_Sample')
        return output.getvalue()

    st.download_button(label=f"📥 Download {currency_choice} Sample", data=get_xlsx_sample(generate_large_sample(currency_choice)), file_name=f"sample.xlsx")
    
    st.divider()
    st.subheader("2. Upload & Settings")
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    risk_threshold = st.slider("Select Risk Threshold (%)", 0, 100, 70)

# --- 3. DATA LOADING (DENGAN DATA AWAL 200 BARIS) ---
def load_data(file):
    if file is not None:
        try:
            df_loaded = pd.read_csv(file, sep=None, engine='python') if file.name.endswith('.csv') else pd.read_excel(file)
            return df_loaded
        except: return pd.DataFrame()
    else:
        # INI ADALAH DATA AWAL 200 BARIS SEPERTI YANG ANDA MINTA
        user_list = [f"User-0{i}" for i in range(1, 7)]
        data = {
            'Transaction_ID': [f"TRX-2024-{i:04d}" for i in range(1, 201)],
            'Date': pd.date_range(start='2024-01-01', periods=200, freq='D'),
            'User_ID': np.random.choice(user_list, 200),
            'Vendor': np.random.choice(['Vendor X', 'Vendor Y', 'Vendor Z', 'Vendor K', 'Vendor L'], 200),
            'Amount': np.random.uniform(1000, 50000, 200).round(2),
            'Description': 'Purchase Order Initial Data'
        }
        return pd.DataFrame(data)

df = load_data(uploaded_file)

# --- 4. ANALYTICS ENGINE (PERBAIKAN ERROR PD.CUT) ---
if not df.empty:
    # Cari kolom angka
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    target_col = next((c for c in numeric_cols if any(k in c.lower() for k in ['amount', 'nilai', 'total'])), numeric_cols[0])
    is_rupiah = df[target_col].mean() > 100000

    # Hitung Score
    max_val = df[target_col].max() if df[target_col].max() > 0 else 1
    df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)
    df['Is_Round'] = df[target_col].apply(lambda x: 1 if x % 100 == 0 else 0)
    df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 20)).clip(0, 100)

    # --- BAGIAN KRUSIAL: ANTI ERROR LAYAR MERAH ---
    low_limit = 40
    # Menggunakan sorted list set agar pembatas (bins) selalu unik meskipun slider di 0 atau 100
    risk_bins = sorted(list(set([0, low_limit, risk_threshold, 100])))
    
    # Label harus menyesuaikan jumlah bins yang dihasilkan
    risk_labels = ['Low', 'Medium', 'Critical']
    actual_labels = risk_labels[:len(risk_bins)-1]

    df['Risk_Level'] = pd.cut(df['Final_Score'], bins=risk_bins, labels=actual_labels, include_lowest=True)
    anomalies = df[df['Final_Score'] >= risk_threshold].copy()

# --- 5. DASHBOARD UI ---
st.title("🛡️ AUDIT INTELLIGENCE CORE SYSTEMS")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Transactions", f"{len(df):,}")
col2.metric("Detected Anomalies", f"{len(anomalies):,}", delta=f"{(len(anomalies)/len(df)*100):.1f}%", delta_color="inverse")

with col3:
    total_val = anomalies[target_col].sum()
    st.metric(f"Total Exposure", f"{'Rp ' if is_rupiah else '$'}{total_val:,.0f}")

with col4:
    avg_risk = anomalies['Final_Score'].mean() if not anomalies.empty else 0
    st.metric("Avg Risk Score", f"{avg_risk:,.1f}")

st.divider()

c1, c2 = st.columns([6, 4])
with c1:
    st.subheader("Transaction Risk Distribution")
    fig = px.scatter(df, x=df.index, y=target_col, color="Final_Score", color_continuous_scale='RdYlGn_r')
    fig.add_hline(y=(risk_threshold/100)*max_val, line_dash="dash", line_color="red")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("Risk Category Breakdown")
    risk_counts = df['Risk_Level'].value_counts().reset_index()
    fig_pie = px.pie(risk_counts, values='count', names='Risk_Level', hole=0.4,
                     color='Risk_Level', color_discrete_map={'Critical':'#ef553b', 'Medium':'#fecb52', 'Low':'#00cc96'})
    st.plotly_chart(fig_pie, use_container_width=True)

st.subheader("🚩 Anomaly Investigation List")
st.dataframe(anomalies.sort_values(by='Final_Score', ascending=False), use_container_width=True, hide_index=True)

if not anomalies.empty:
    st.download_button("📥 Download Audit Report", data=get_xlsx_sample(anomalies), file_name="Audit_Report.xlsx")
