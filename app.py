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
    st.info("Developed by: saidUhuud | Quantitative Developer & Statistical Consultant")
    
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    
    st.divider()
# --- MODUL SIDEBAR: DOWNLOAD SAMPLE DATA ---
# Letakkan ini tepat di bawah st.divider() yang ada di dalam 'with st.sidebar:'

    st.subheader("1. Data Sample")

# --- POTONGAN DOWNLOAD SAMPLE DENGAN XLSXWRITER ---

    # Fungsi untuk membuat file Excel yang rapi
    def get_xlsx_sample(df_sample):
        output = BytesIO()
        # Inisialisasi xlsxwriter
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_sample.to_excel(writer, index=False, sheet_name='Audit_Sample')
            
            workbook  = writer.book
            worksheet = writer.sheets['Audit_Sample']
            
            # 1. Format Header: Tebal, Latar Biru Tua, Teks Putih
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'vcenter',
                'align': 'center',
                'fg_color': '#1F4E78',
                'font_color': 'white',
                'border': 1
            })
            
            # 2. Format Angka: Pemisah ribuan (Misal: 1,000,000.00)
            currency_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
            border_format = workbook.add_format({'border': 1})

            # Terapkan format ke Header dan Atur Lebar Kolom otomatis
            for col_num, value in enumerate(df_sample.columns.values):
                worksheet.write(0, col_num, value, header_format)
                column_len = max(df_sample[value].astype(str).map(len).max(), len(value)) + 3
                
                # Jika kolom adalah 'Amount', berikan format angka
                if value == 'Amount':
                    worksheet.set_column(col_num, col_num, column_len, currency_format)
                else:
                    worksheet.set_column(col_num, col_num, column_len, border_format)
                    
        return output.getvalue()

    sample_df = generate_large_sample()
    
    # Fungsi konversi ke CSV
    def convert_df_to_csv(df_to_convert):
        return df_to_convert.to_csv(index=False).encode('utf-8')

    st.download_button(
        label="📥 Download 1,500 Rows Sample (CSV)",
        data=convert_df_to_csv(sample_df),
        file_name="audit_sample_saiduhuud.csv",
        mime="text/csv",
    )
    st.caption("No data? Download this sample to test the app")
    
    st.divider()
    st.subheader("2. Dashboard Settings")
    # Bagian slider risk_threshold tetap berada di bawah sini
    
    risk_threshold = st.slider("Select Risk Threshold (%)", 0, 100, 70)
    st.caption("Transactions above this score will be flagged as High Risk.")

# --- MOCK DATA GENERATOR (Jika user belum upload file) ---
def load_data(file):
    if file is not None:
        if file.name.endswith('.csv'):
            return pd.read_csv(file)
        else:
            return pd.read_excel(file)
    else:
        # Data dummy untuk demonstrasi awal
        data = {
            'Date': pd.date_range(start='2024-01-01', periods=200, freq='D'),
            'Vendor': np.random.choice(['Vendor A', 'Vendor B', 'Vendor C', 'Global Corp', 'Indo Jaya'], 200),
            'Amount': np.random.uniform(1000, 50000, 200).round(2),
            'Description': 'Purchase Order'
        }
        return pd.DataFrame(data)

df = load_data(uploaded_file)

# --- AUDIT LOGIC (Postingan 8 Integration) ---
# Menghitung Risk Score sederhana: Berdasarkan nilai transaksi dan angka bulat (Round Number)
df['Risk_Score'] = (df['Amount'] / df['Amount'].max() * 100).round(2)
df['Is_Round'] = df['Amount'].apply(lambda x: 1 if x % 100 == 0 else 0)
df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 20)).clip(0, 100)

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



