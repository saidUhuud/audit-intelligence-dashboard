import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- CONFIG & BRANDING ---
st.set_page_config(page_title="saidUhuud | Quantitative Developer", layout="wide")

# Styling Dashboard
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 2px 2px 8px rgba(0,0,0,0.05); }
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3201/3201521.png", width=80)
    st.title("Audit Control Panel")
    st.info("Developed by: **saidUhuud**\n\n*Quantitative Developer | Statistical Consultant*")
    
    st.divider()
    st.subheader("1. Download Sample")
    # Sample 1500 baris untuk dicoba orang lain
    sample_data = pd.DataFrame({
        'Transaction_ID': [f'SMP-{i:04d}' for i in range(1, 1501)],
        'Date': pd.date_range(start='2025-01-01', periods=1500, freq='H'),
        'Vendor_Name': np.random.choice(['Supplier A', 'Supplier B', 'Supplier C'], 1500),
        'Amount': np.random.uniform(1000000, 100000000, 1500).round(2)
    })
    st.download_button("📥 Download 1,500 Rows Sample", sample_data.to_csv(index=False).encode('utf-8'), "audit_sample_1500.csv", "text/csv")
    
    st.divider()
    st.subheader("2. Settings")
    uploaded_file = st.file_uploader("Upload Data Anda", type=['csv', 'xlsx'])
    
    # THRESHOLD: Remote control utama untuk real-time update
    risk_threshold = st.slider("Set Risk Threshold (%)", 0, 100, 75)

# --- DATA ENGINE ---
@st.cache_data
def get_default_data():
    # Munculkan 500 data awal agar dashboard tidak kosong
    np.random.seed(42)
    return pd.DataFrame({
        'Date': pd.date_range(start='2025-01-01', periods=500),
        'Vendor': np.random.choice(['Alpha Tech', 'Beta Corp', 'Gamma Inc'], 500),
        'Amount': np.random.uniform(5000000, 50000000, 500).round(2)
    })

# Load data (Upload vs Default)
if uploaded_file:
    df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
else:
    df = get_default_data()

# LOGIKA DINAMIS HEADER (Cari kolom nominal uang secara otomatis)
numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
target_col = "Amount" # default
if numeric_cols:
    for col in numeric_cols:
        if any(x in col.lower() for x in ['amount', 'nilai', 'total', 'harga']):
            target_col = col
            break

# --- REAL-TIME CALCULATION ---
# Seluruh perhitungan di bawah ini akan bergerak SETIAP KALI slider digeser
max_val = df[target_col].max() if df[target_col].max() > 0 else 1
df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)

# Filter Anomali berdasarkan Slider
anomalies = df[df['Risk_Score'] >= risk_threshold].copy()

# --- UI DASHBOARD ---
st.title("🛡️ Quantitative Audit Dashboard")
st.warning("👈 **Mobile Users: Open the sidebar menu (top-left) to access controls and adjust threshold.**")

# 1. Metrics (Bergerak Real-Time)
m1, m2, m3 = st.columns(3)
m1.metric("Transactions Analyzed", f"{len(df):,}")
m2.metric("High Risk Anomalies", f"{len(anomalies):,}", 
          delta=f"{(len(anomalies)/len(df)*100):.1f}% dari total", delta_color="inverse")
m3.metric("Total Exposure Value", f"${anomalies[target_col].sum():,.2f}")

st.divider()

# 2. Visualizations (Bergerak Real-Time)
col_left, col_right = st.columns([7, 3])

with col_left:
    st.subheader("📊 Statistical Risk Landscape")
    # Warna grafik mengikuti Risk Score
    fig = px.scatter(df, x=df.index, y=target_col, color="Risk_Score", 
                     size=target_col, color_continuous_scale='RdYlGn_r',
                     title=f"Mapping {len(df)} Data Points")
    # Tambahkan garis ambang batas (Threshold Line) agar visual lebih presisi
    fig.add_hline(y=(risk_threshold/100)*max_val, line_dash="dash", line_color="red", annotation_text="Risk Threshold")
    st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.subheader("🎯 Risk Segments")
    # Segmentasi ikut berubah real-time sesuai slider
    risk_labels = ['Low', 'Medium', 'High']
    # Dinamis bins berdasarkan slider
    df['Category'] = pd.cut(df['Risk_Score'], bins=[0, risk_threshold-20, risk_threshold, 100], 
                            labels=risk_labels, include_lowest=True)
    seg_data = df['Category'].value_counts()
    fig_pie = px.pie(values=seg_data.values, names=seg_data.index, hole=0.4,
                     color=seg_data.index, color_discrete_map={'High':'#FF4B4B', 'Medium':'#FFAA00', 'Low':'#00CC96'})
    st.plotly_chart(fig_pie, use_container_width=True)

# 3. Investigation Table
st.subheader("🚩 Investigation Priority List")
st.dataframe(anomalies.sort_values(by='Risk_Score', ascending=False), use_container_width=True)

# --- EXPORT EXCEL RAPI ---
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
    st.download_button("📥 Export Anomalies to Excel", get_xlsx_download(anomalies), 
                       "Quantitative_Audit_Report.xlsx", "application/vnd.ms-excel")
