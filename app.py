import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- CONFIGURATION ---
st.set_page_config(page_title="saidUhuud | Quantitative Developer", layout="wide")

# Custom CSS untuk tampilan premium
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 2px 2px 8px rgba(0,0,0,0.05); }
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# --- GENERATE DATA AWAL (500 BARIS) ---
@st.cache_data
def generate_initial_data():
    np.random.seed(42)
    return pd.DataFrame({
        'Date': pd.date_range(start='2025-01-01', periods=500),
        'Vendor_Name': np.random.choice(['Alpha Corp', 'Beta Ltd', 'Gamma Inc', 'Delta Co'], 500),
        'Amount': np.random.uniform(5000, 50000, 500).round(2),
        'Description': 'Initial Audit Sample'
    })

initial_df = generate_initial_data()

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3201/3201521.png", width=80)
    st.title("Audit Control Panel")
    st.info("Developed by: **saidUhuud**\n\n*Quantitative Developer | Statistical Consultant*")
    
    st.divider()
    st.subheader("1. Sample for Testing")
    # Sampel 1.500 data untuk diunduh audiens
    sample_dl = pd.DataFrame({
        'Tanggal': pd.date_range(start='2025-01-01', periods=1500, freq='H'),
        'Vendor': np.random.choice(['Supplier A', 'Supplier B', 'Supplier C'], 1500),
        'Total_Nilai': np.random.uniform(1000000, 100000000, 1500).round(2)
    })
    st.download_button("📥 Download 1,500 Rows Sample", sample_dl.to_csv(index=False).encode('utf-8'), "audit_sample_1500.csv", "text/csv")
    
    st.divider()
    st.subheader("2. Upload & Settings")
    uploaded_file = st.file_uploader("Upload Data (CSV/Excel)", type=['csv', 'xlsx'])
    risk_threshold = st.slider("Risk Threshold (%)", 0, 100, 75)

# --- DATA PROCESSING ---
df = initial_df
target_col = "Amount"

if uploaded_file:
    # Membaca file yang diupload (baik CSV maupun Excel)
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
    
    # DINAMIS: Mencari kolom angka agar tidak error meski header beda
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if numeric_cols:
        # Default pilih kolom yang ada kata 'Amount' atau 'Nilai'
        default_idx = 0
        for i, col in enumerate(numeric_cols):
            if any(x in col.lower() for x in ['amount', 'nilai', 'total', 'harga']):
                default_idx = i
                break
        target_col = st.sidebar.selectbox("Pilih Kolom Nilai Uang:", numeric_cols, index=default_idx)
    else:
        st.error("File tidak memiliki kolom angka!")

# Logika Risiko (Berdasarkan Nilai Tertinggi)
max_val = df[target_col].max() if df[target_col].max() > 0 else 1
df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)
anomalies = df[df['Risk_Score'] >= risk_threshold].copy()

# --- MAIN DASHBOARD ---
st.title("🛡️ Quantitative Audit Dashboard")
st.warning("👈 **Mobile Users: Open the sidebar menu (top-left) to access controls.**")

# Row 1: Metrics
m1, m2, m3 = st.columns(3)
m1.metric("Rows Analyzed", f"{len(df):,}")
m2.metric("High Risk Anomalies", f"{len(anomalies):,}")
m3.metric("Total Exposure", f"${df[target_col].sum():,.2f}")

st.divider()

# Row 2: Visualizations
c1, c2 = st.columns([7, 3])
with c1:
    st.subheader("📊 Statistical Risk Landscape")
    fig = px.scatter(df, x=df.index, y=target_col, color="Risk_Score", 
                     size=target_col, color_continuous_scale='RdYlGn_r')
    st.plotly_chart(fig, use_container_width=True)
with c2:
    st.subheader("🎯 Risk Segments")
    risk_cat = pd.cut(df['Risk_Score'], bins=[0, 40, 75, 100], labels=['Low', 'Medium', 'High']).value_counts()
    fig_pie = px.pie(values=risk_cat.values, names=risk_cat.index, hole=0.4,
                     color=risk_cat.index, color_discrete_map={'High':'#FF4B4B', 'Medium':'#FFAA00', 'Low':'#00CC96'})
    st.plotly_chart(fig_pie, use_container_width=True)

# Row 3: Investigation List
st.subheader("🚩 Investigation Priority List")
st.dataframe(anomalies.sort_values(by='Risk_Score', ascending=False), use_container_width=True)

# --- RAPI EXPORT DENGAN XLSXWRITER ---
def get_xlsx_download(df_export):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_export.to_excel(writer, index=False, sheet_name='Audit_Report')
        workbook  = writer.book
        worksheet = writer.sheets['Audit_Report']
        
        # Format Header
        header_fmt = workbook.add_format({'bold': True, 'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1})
        for col_num, value in enumerate(df_export.columns.values):
            worksheet.write(0, col_num, value, header_fmt)
            column_len = max(df_export[value].astype(str).map(len).max(), len(value)) + 2
            worksheet.set_column(col_num, col_num, column_len)
    return output.getvalue()

if not anomalies.empty:
    st.download_button("📥 Export Results to Excel", get_xlsx_download(anomalies), 
                       "Audit_Report_saidUhuud.xlsx", "application/vnd.ms-excel")
