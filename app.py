import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

st.set_page_config(page_title="saidUhuud | Quantitative Developer", layout="wide")

# --- GENERATE 500 DATA AWAL (DEFAULT) ---
@st.cache_data
def generate_initial_data():
    np.random.seed(42)
    return pd.DataFrame({
        'Date': pd.date_range(start='2024-01-01', periods=500),
        'Vendor': np.random.choice(['Vendor A', 'Vendor B', 'Vendor C'], 500),
        'Amount': np.random.randint(1000, 50000, 500),
        'Description': 'Initial Sample Data'
    })

initial_df = generate_initial_data()

# --- SIDEBAR ---
with st.sidebar:
    st.title("Audit Control Panel")
    st.info("Developed by: **saidUhuud**\n\n*Quantitative Developer | Statistical Consultant*")
    
    st.divider()
    st.subheader("1. Sample for Testing")
    # Menggunakan data 1.500 untuk sampel download (sesuai request Anda)
    sample_for_dl = pd.DataFrame({
        'Tanggal': pd.date_range(start='2024-01-01', periods=1500),
        'Nama_Vendor': np.random.choice(['Supplier A', 'Supplier B', 'Supplier C','Supplier D','Supplier E','Supplier F'], 1500),
        'Total_Nilai': np.random.randint(5000, 100000, 1500)
    })
    st.download_button("📥 Download 1,500 Sample Rows", 
                       sample_for_dl.to_csv(index=False).encode('utf-8'), 
                       "audit_sample_1500.csv", "text/csv")
    
    st.divider()
    st.subheader("2. Upload Data")
    uploaded_file = st.file_uploader("Upload File (Header bebas)", type=['csv', 'xlsx'])
    
    risk_threshold = st.slider("Risk Threshold (%)", 0, 100, 75)

# --- LOGIC PENANGANAN HEADER BERBEDA ---
df = initial_df # Default awal 500 data
target_col = "Amount" # Default target

if uploaded_file:
    if uploaded_file.name.endswith('.csv'): df = pd.read_csv(uploaded_file)
    else: df = pd.read_excel(uploaded_file)
    
    st.sidebar.success("File Uploaded!")
    
    # Deteksi otomatis kolom angka
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if numeric_cols:
        target_col = st.sidebar.selectbox("Pilih kolom yang berisi NILAI uang:", numeric_cols)
    else:
        st.error("Tidak ditemukan kolom angka di file Anda!")

# --- PROCESSING (Tetap menggunakan logika mentah Anda) ---
if df is not None:
    # Menggunakan kolom yang dipilih user (target_col) sebagai pengganti 'Amount'
    max_val = df[target_col].max() if df[target_col].max() > 0 else 1
    df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)
    df['Final_Score'] = df['Risk_Score'].clip(0, 100) # Logika disederhanakan agar tidak error

    anomalies = df[df['Final_Score'] >= risk_threshold]

    # --- UI DASHBOARD ---
    st.title("🛡️ Quantitative Audit Dashboard")
    st.warning("👈 **Mobile Users: Open the sidebar menu to change settings or upload files.**")

    m1, m2, m3 = st.columns(3)
    m1.metric("Rows Analyzed", f"{len(df):,}")
    m2.metric("Critical Anomalies", f"{len(anomalies):,}")
    m3.metric("Total Value", f"{df[target_col].sum():,.2f}")

    st.subheader("📊 Risk Landscape")
    # Mencari kolom tanggal secara otomatis untuk sumbu X
    date_col = df.columns[0] # Default kolom pertama
    fig = px.scatter(df, x=df.index, y=target_col, color="Final_Score", size=target_col)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("🚩 Investigation List")
    st.dataframe(anomalies, use_container_width=True)
