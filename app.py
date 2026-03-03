import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- CONFIG & UI ---
st.set_page_config(page_title="saidUhuud | Quantitative Dashboard", layout="wide")

# --- GENERATE DATA AWAL (500 BARIS) ---
@st.cache_data
def generate_initial_data():
    np.random.seed(42)
    return pd.DataFrame({
        'Transaction_ID': [f'TXN-{i:04d}' for i in range(1, 501)],
        'Date': pd.date_range(start='2025-01-01', periods=500),
        'Vendor_Name': np.random.choice(['Global Tech Corp', 'Indo Jaya Logistik', 'Cahaya Konstruksi', 'Mandiri Perkasa PT'], 500),
        'Amount': np.random.uniform(5000000, 50000000, 500).round(2),
        'Status': 'Finalized'
    })

initial_df = generate_initial_data()

# --- SIDEBAR ---
with st.sidebar:
    st.title("Audit Control Panel")
    st.info("Developed by: **saidUhuud**\n\n*Quantitative Developer | Statistical Consultant*")
    
    # SAMPLE DATA 1500 UNTUK DOWNLOAD
    st.subheader("1. Download Sample")
    sample_dl = pd.DataFrame({
        'Transaction_ID': [f'SMP-{i:04d}' for i in range(1, 1501)],
        'Date': pd.date_range(start='2025-01-01', periods=1500, freq='H'),
        'Vendor_Name': np.random.choice(['Supplier Alpha', 'Supplier Beta', 'Gamma Ltd'], 1500),
        'Amount': np.random.uniform(1000000, 100000000, 1500).round(2)
    })
    st.download_button("📥 Download 1,500 Rows Sample", sample_dl.to_csv(index=False).encode('utf-8'), "sample_audit.csv", "text/csv")
    
    st.divider()
    st.subheader("2. Upload & Settings")
    uploaded_file = st.file_uploader("Upload Data Anda", type=['csv', 'xlsx'])
    risk_threshold = st.slider("Risk Threshold (%)", 0, 100, 80)

# --- LOGIC PEMROSESAN ---
df = initial_df
target_col = "Amount"

if uploaded_file:
    df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if numeric_cols:
        # Cari kolom yang namanya mirip 'Amount' atau 'Nilai'
        default_idx = 0
        for i, col in enumerate(numeric_cols):
            if any(x in col.lower() for x in ['amount', 'nilai', 'total', 'price']):
                default_idx = i
                break
        target_col = st.sidebar.selectbox("Pilih Kolom Nilai Uang:", numeric_cols, index=default_idx)

# Hitung Skor Risiko Sederhana
max_val = df[target_col].max() if df[target_col].max() > 0 else 1
df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)
anomalies = df[df['Risk_Score'] >= risk_threshold].copy()

# --- DASHBOARD UI ---
st.title("🛡️ Quantitative Audit Dashboard")
st.warning("👈 **Mobile Users: Klik panah di pojok kiri atas untuk kontrol dashboard.**")

# Tampilan Utama
st.metric("Total Exposure Analyzed", f"${df[target_col].sum():,.2f}")
st.subheader(f"🚩 High Risk Anomalies (>{risk_threshold}%)")
st.dataframe(anomalies, use_container_width=True)

# --- FUNGSI DOWNLOAD EXCEL RAPI (xlsxwriter) ---
def get_xlsx_download(df_to_export):
    output = BytesIO()
    # Menggunakan engine xlsxwriter
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_to_export.to_excel(writer, index=False, sheet_name='Audit_Report')
        
        workbook  = writer.book
        worksheet = writer.sheets['Audit_Report']
        
        # Format: Header (Bold, Background Blue, Text White)
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#1F4E78',
            'font_color': 'white',
            'border': 1
        })
        
        # Format: Currency/Number
        currency_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
        border_format = workbook.add_format({'border': 1})

        # Terapkan format ke header secara manual
        for col_num, value in enumerate(df_to_export.columns.values):
            worksheet.write(0, col_num, value, header_format)
            # Auto-adjust kolom lebar
            column_len = max(df_to_export[value].astype(str).map(len).max(), len(value)) + 2
            worksheet.set_column(col_num, col_num, column_len)
            
        # Cari index kolom target (Amount) untuk diberi format mata uang
        try:
            target_idx = df_to_export.columns.get_loc(target_col)
            worksheet.set_column(target_idx, target_idx, 18, currency_format)
        except:
            pass

    return output.getvalue()

# Tombol Download Excel
if not anomalies.empty:
    st.download_button(
        label="📥 Download Rapi (Excel Format)",
        data=get_xlsx_download(anomalies),
        file_name="Audit_Investigation_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
