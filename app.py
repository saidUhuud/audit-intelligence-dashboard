import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- CONFIGURATION & UI ---
st.set_page_config(page_title="saidUhuud | Audit Intelligence Dashboard", layout="wide")

# Custom CSS untuk tampilan premium
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 2px 2px 8px rgba(0,0,0,0.05); }
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR (INPUT & SAMPLE DATA) ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3201/3201521.png", width=80)
    st.title("Audit Control Panel")
    st.info("Developed by: **saidUhuud** | Python Audit Expert")
    
    st.divider()
    st.subheader("1. Get Started")
    
    # Fitur Download Sample Data
    sample_data = pd.DataFrame({
        'Date': pd.date_range(start='2024-01-01', periods=50),
        'Vendor': np.random.choice(['Vendor A', 'Vendor B', 'Global Corp', 'Indo Jaya'], 50),
        'Amount': [1000, 5000, 15000, 200, 45000, 100000, 300, 1250, 400, 10000] * 5,
        'Description': 'Regular Payment'
    })
    
    def convert_df(df):
        return df.to_csv(index=False).encode('utf-8')

    st.download_button(
        label="📥 Download Sample Data CSV",
        data=convert_df(sample_data),
        file_name="audit_sample_data.csv",
        mime="text/csv",
    )
    st.caption("No data? Download this sample to test the app.")
    
    st.divider()
    st.subheader("2. Upload & Analysis")
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    
    risk_threshold = st.slider("Set Risk Threshold (%)", 0, 100, 70)
    st.caption("Scores above this will be flagged for investigation.")

# --- DATA LOADING ---
def load_data(file):
    if file is not None:
        try:
            if file.name.endswith('.csv'):
                return pd.read_csv(file)
            else:
                return pd.read_excel(file)
        except Exception as e:
            st.error(f"Error loading file: {e}")
            return None
    else:
        # Tampilan awal menggunakan data sample
        return sample_data

df = load_data(uploaded_file)

if df is not None:
    # --- AUDIT LOGIC (Postingan 8) ---
    # Logika deteksi risiko sederhana
    max_val = df['Amount'].max() if df['Amount'].max() > 0 else 1
    df['Risk_Score'] = (df['Amount'] / max_val * 100).round(2)
    df['Is_Round'] = df['Amount'].apply(lambda x: 1 if x % 1000 == 0 else 0)
    df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 15)).clip(0, 100)

    # Filter Anomali
    anomalies = df[df['Final_Score'] >= risk_threshold]

    # --- MAIN DASHBOARD UI ---
    st.title("🛡️ Audit Intelligence & Risk Dashboard")
    
    # Petunjuk Khusus Pengguna HP (Penting!)
    st.warning("👈 **Buka menu di pojok kiri atas (ikon panah) untuk Upload Data atau atur Threshold jika Anda menggunakan HP.**")

    # Row 1: Key Metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Transactions", f"{len(df):,}")
    m2.metric("Anomalies Found", f"{len(anomalies):,}", delta=f"{(len(anomalies)/len(df)*100):.1f}% Risk Rate", delta_color="inverse")
    m3.metric("Total Value Analyzed", f"${df['Amount'].sum():,.2f}")

    st.divider()

    # Row 2: Visualizations
    col_a, col_b = st.columns([7, 3])

    with col_a:
        st.subheader("📊 Transaction Risk Landscape")
        fig = px.scatter(df, x=df.index, y="Amount", color="Final_Score", 
                         size="Amount", color_continuous_scale='RdYlGn_r',
                         hover_data=['Vendor', 'Description'],
                         title="Risk Mapping (Click and drag to zoom)")
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("🎯 Risk Segmentation")
        risk_cat = pd.cut(df['Final_Score'], bins=[0, 30, 70, 100], labels=['Low', 'Medium', 'High']).value_counts()
        fig_pie = px.pie(values=risk_cat.values, names=risk_cat.index, 
                         color=risk_cat.index, color_discrete_map={'High':'#FF4B4B', 'Medium':'#FFAA00', 'Low':'#00CC96'},
                         hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)

    # Row 3: Investigation Table
    st.subheader("🚩 Anomaly Investigation List")
    st.write("Daftar transaksi yang melebihi batas risiko Anda:")
    st.dataframe(anomalies.sort_values(by='Final_Score', ascending=False), use_container_width=True)

    # --- EXPORT ---
    def to_excel(df):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Audit_Report')
        return output.getvalue()

    st.download_button(
        label="📥 Download Audit Investigation Report (Excel)",
        data=to_excel(anomalies),
        file_name="Audit_Anomaly_Report.xlsx",
        mime="application/vnd.ms-excel"
    )

st.sidebar.markdown("---")
st.sidebar.success("✅ App is Live and Secure")
