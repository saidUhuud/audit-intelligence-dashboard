import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# --- CONFIGURATION & UI ---
st.set_page_config(page_title="saidUhuud | Audit Intelligence Dashboard", layout="wide")

# Custom CSS untuk mempercantik tampilan
st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 2px 2px 10px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR (INPUT) ---
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3201/3201521.png", width=100)
    st.title("Audit Control Panel")
    st.info("Developed by: saidUhuud")
    
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    
    st.divider()
    risk_threshold = st.slider("Select Risk Threshold (%)", 0, 100, 70)
    st.caption("Transactions above this score will be flagged as High Risk.")
    
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

# --- DASHBOARD UI (Postingan 9 Integration) ---
st.title("🛡️ Audit Intelligence & Risk Dashboard")
st.markdown("Transforming raw transactions into actionable audit insights.")

# Row 1: Key Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Transactions", len(df))
col2.metric("Detected Anomalies", len(anomalies), delta=f"{len(anomalies)/len(df)*100:.1f}%", delta_color="inverse")
col3.metric("Total Amount Analyzed", f"${df['Amount'].sum():,.0f}")
col4.metric("Avg Risk Score", f"{df['Final_Score'].mean():,.1f}")

st.divider()

# Row 2: Visualizations
c1, c2 = st.columns([6, 4])

with c1:
    st.subheader("Transaction Risk Distribution")
    fig = px.scatter(df, x="Date", y="Amount", color="Final_Score", 
                     size="Amount", color_continuous_scale='RdYlGn_r',
                     title="Visualizing Risks over Time")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("Risk Category Breakdown")
    risk_counts = pd.cut(df['Final_Score'], bins=[0, 40, 70, 100], labels=['Low', 'Medium', 'High']).value_counts()
    fig_pie = px.pie(values=risk_counts.values, names=risk_counts.index, 
                     color=risk_counts.index, color_discrete_map={'High':'#ef553b', 'Medium':'#fecb52', 'Low':'#00cc96'})
    st.plotly_chart(fig_pie, use_container_width=True)

# Row 3: Data Table
st.subheader("🚩 Anomaly Investigation List")
st.dataframe(anomalies.sort_values(by='Final_Score', ascending=False), use_container_width=True)

# --- EXPORT TO EXCEL ---
def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Audit_Report')
    return output.getvalue()

st.download_button(
    label="📥 Download Audit Report (Excel)",
    data=to_excel(anomalies),
    file_name="Audit_Anomaly_Report_saidUhuud.xlsx",
    mime="application/vnd.ms-excel"
)

st.sidebar.success("App Status: Ready for Audit")
