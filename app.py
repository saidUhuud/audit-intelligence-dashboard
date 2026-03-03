import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- CONFIGURATION & UI ---
st.set_page_config(page_title="saidUhuud | Quantitative Audit Dashboard", layout="wide")

# Custom CSS Premium
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
    # Identitas Baru sesuai Profil Anda
    st.info("Developed by: **saidUhuud**\n\n*Quantitative Developer | Statistical Consultant*")
    
    st.divider()
    st.subheader("1. Get Started")
    
    # Generate 1.500 Data Sample yang lebih realistis
    @st.cache_data
    def generate_large_sample():
        np.random.seed(42)
        data = {
            'Date': pd.date_range(start='2024-01-01', periods=1500, freq='H'),
            'Vendor': np.random.choice(['Vendor A', 'Vendor B, 'Vendor C', 'Vendor D', 'Vendor E', 'Vendor F'], 1500),
            'Amount': np.random.lognormal(mean=8, sigma=1.2, size=1500).round(2),
            'Description': np.random.choice(['Service Fee', 'Procurement', 'Maintenance', 'Operational', 'Reimbursement'], 1500)
        }
        return pd.DataFrame(data)

    sample_data = generate_large_sample()
    
    def convert_df(df):
        return df.to_csv(index=False).encode('utf-8')

    st.download_button(
        label="📥 Download 1,500 Sample Data (CSV)",
        data=convert_df(sample_data),
        file_name="audit_large_sample_1500.csv",
        mime="text/csv",
    )
    st.caption("Gunakan file ini untuk simulasi audit skala besar.")
    
    st.divider()
    st.subheader("2. Upload & Analysis")
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    
    risk_threshold = st.slider("Set Risk Threshold (%)", 0, 100, 75)
    
    # Fitur Search Vendor
    search_query = st.text_input("🔍 Search Vendor", "")

# --- DATA LOADING ---
def load_data(file):
    if file is not None:
        try:
            if file.name.endswith('.csv'): return pd.read_csv(file)
            else: return pd.read_excel(file)
        except Exception as e:
            st.error(f"Error: {e}")
            return None
    return sample_data

df = load_data(uploaded_file)

if df is not None:
    # --- QUANTITATIVE LOGIC ---
    max_val = df['Amount'].max() if df['Amount'].max() > 0 else 1
    df['Risk_Score'] = (df['Amount'] / max_val * 100).round(2)
    # Deteksi angka bulat sebagai indikator risiko audit
    df['Is_Round'] = df['Amount'].apply(lambda x: 1 if x % 100 == 0 else 0)
    df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 20)).clip(0, 100)

    # Filter berdasarkan Threshold & Search
    anomalies = df[df['Final_Score'] >= risk_threshold]
    if search_query:
        anomalies = anomalies[anomalies['Vendor'].str.contains(search_query, case=False)]

    # --- MAIN DASHBOARD UI ---
    st.title("🛡️ Audit Intelligence & Risk Dashboard")
    st.warning("👈 **Open the menu in the top left corner (arrow icon) to navigate features via Handphone**")

    # Row 1: Key Metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Transactions Analyzed", f"{len(df):,}")
    m2.metric("Critical Anomalies", f"{len(anomalies):,}", 
              delta=f"{(len(anomalies)/len(df)*100):.1f}% Risk Rate", delta_color="inverse")
    m3.metric("Total Exposure Value", f"${df['Amount'].sum():,.2f}")

    st.divider()

    # Row 2: Visualizations
    col_a, col_b = st.columns([7, 3])

    with col_a:
        st.subheader("📊 Statistical Risk Distribution")
        fig = px.scatter(df, x="Date", y="Amount", color="Final_Score", 
                         size="Amount", color_continuous_scale='RdYlGn_r',
                         hover_data=['Vendor'],
                         title="Risk Landscape (1,500+ Data Points)")
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("🎯 Risk Segmentation")
        risk_cat = pd.cut(df['Final_Score'], bins=[0, 40, 75, 100], labels=['Low', 'Medium', 'High']).value_counts()
        fig_pie = px.pie(values=risk_cat.values, names=risk_cat.index, 
                         color=risk_cat.index, color_discrete_map={'High':'#FF4B4B', 'Medium':'#FFAA00', 'Low':'#00CC96'},
                         hole=0.45)
        st.plotly_chart(fig_pie, use_container_width=True)

    # Row 3: Table
    st.subheader("🚩 Investigation Priority List")
    st.dataframe(anomalies.sort_values(by='Final_Score', ascending=False), use_container_width=True)

    # --- EXPORT ---
    def to_excel(df_to_save):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_to_save.to_excel(writer, index=False, sheet_name='Audit_Report')
        return output.getvalue()

    st.download_button(
        label="📥 Export Investigation Results to Excel",
        data=to_excel(anomalies),
        file_name="Quantitative_Audit_Report.xlsx",
        mime="application/vnd.ms-excel"
    )

st.sidebar.markdown("---")
st.sidebar.caption("© 2026 saidUhuud | Statistical Consulting Division")
