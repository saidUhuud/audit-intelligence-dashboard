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
    
    st.divider()
    st.subheader("1. Data Sample")

    # --- BAGIAN SAMPLE DATA (XLSXWRITER) ---
    @st.cache_data
    def generate_large_sample():
        np.random.seed(42)
        data_sample = {
            'Date': pd.date_range(start='2025-01-01', periods=1500, freq='H'),
            'Vendor': np.random.choice(['Vendor A', 'Vendor B', 'Vendor C', 'Vendor D', 'Vendor E'], 1500),
            'Amount': np.random.uniform(1000000, 100000000, 1500).round(2),
            'Description': np.random.choice(['Service Fee', 'Procurement', 'Maintenance', 'Operational'], 1500)
        }
        return pd.DataFrame(data_sample)

    sample_df = generate_large_sample()

    def get_xlsx_sample(df_sample):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_sample.to_excel(writer, index=False, sheet_name='Audit_Sample')
            workbook  = writer.book
            worksheet = writer.sheets['Audit_Sample']
            header_format = workbook.add_format({
                'bold': True, 'text_wrap': True, 'valign': 'vcenter',
                'align': 'center', 'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1
            })
            currency_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
            border_format = workbook.add_format({'border': 1})

            for col_num, value in enumerate(df_sample.columns.values):
                worksheet.write(0, col_num, value, header_format)
                column_len = max(df_sample[value].astype(str).map(len).max(), len(value)) + 3
                if value == 'Amount':
                    worksheet.set_column(col_num, col_num, column_len, currency_format)
                else:
                    worksheet.set_column(col_num, col_num, column_len, border_format)
        return output.getvalue()

    st.download_button(
        label="📥 Download Data Sample Here",
        data=get_xlsx_sample(sample_df),
        file_name="audit_sample_1,500 Rows_saidUhuud.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.caption("No data? Download this sample to test!")
    
    st.divider()
    st.subheader("2. Upload & Settings")
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    
    risk_threshold = st.slider("Select Risk Threshold (%)", 0, 100, 70)
    st.caption("Transactions above this score will be flagged as High Risk.")

# --- MOCK DATA GENERATOR (VERSI TAHAN BANTING) ---
def load_data(file):
    if file is not None:
        try:
            if file.name.endswith('.csv'):
                # Menangani ribuan titik dan desimal koma di CSV
                df_loaded = pd.read_csv(file, sep=None, engine='python')
            else:
                # Menangani Excel
                df_loaded = pd.read_excel(file)
            
            # --- PEMBERSIHAN OTOMATIS (DATA CLEANSING) ---
            # Cari semua kolom yang harusnya angka tapi terbaca teks karena format Rp atau titik/koma
            for col in df_loaded.columns:
                if df_loaded[col].dtype == 'object':
                    # Cek jika kolom mengandung karakter angka
                    sample_val = str(df_loaded[col].dropna().iloc[0]) if not df_loaded[col].dropna().empty else ""
                    if any(char.isdigit() for char in sample_val) and any(c in sample_val for c in [',', '.']):
                        # Bersihkan simbol Rp, spasi, dan titik ribuan, lalu ubah koma jadi titik desimal
                        df_loaded[col] = df_loaded[col].astype(str).str.replace(r'[Rp\s.]', '', regex=True).str.replace(',', '.')
                        # Ubah ke numerik, jika gagal biarkan jadi NaN
                        df_loaded[col] = pd.to_numeric(df_loaded[col], errors='coerce')
            
            return df_loaded
        except Exception as e:
            st.error(f"Error loading file: {e}")
            return pd.DataFrame()
    else:
        # Data dummy awal (tetap sesuai pondasi)
        data = {
            'Date': pd.date_range(start='2024-01-01', periods=200, freq='D'),
            'Vendor': np.random.choice(['Vendor X', 'Vendor Y', 'Vendor Z', 'Vendor K', 'Vendor L'], 200),
            'Amount': np.random.uniform(1000, 50000, 200).round(2),
            'Description': 'Purchase Order'
        }
        return pd.DataFrame(data)

# --- 1. LOGIK DINAMIS & REAL-TIME ---

# Deteksi kolom angka secara otomatis agar tidak error
numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
target_col = "Amount" # default

if numeric_cols:
    for col in numeric_cols:
        if any(x in col.lower() for x in ['amount', 'nilai', 'total', 'harga', 'price']):
            target_col = col
            break

# PERHITUNGAN ULANG (Setiap kali slider risk_threshold digeser, bagian ini dihitung ulang)
max_val = df[target_col].max() if df[target_col].max() > 0 else 1
df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)
df['Is_Round'] = df[target_col].apply(lambda x: 1 if x % 100 == 0 else 0)
df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 20)).clip(0, 100)

# Filter Anomali (Sangat bergantung pada slider risk_threshold secara real-time)
anomalies = df[df['Final_Score'] >= risk_threshold].copy()

# --- 2. UPDATE UI DASHBOARD (REAL-TIME) ---

st.title("🛡️ Python AI (Audit Intelligence) Dashboard")
st.markdown("Transforming raw transactions into actionable audit insights!")
st.warning("👈 **MOBILE USERS: Please open the sidebar menu (arrow icon) to upload data and adjust threshold**")

# Row 1: Key Metrics (Ikut berubah saat slider digeser)
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Transactions", f"{len(df):,}")
col2.metric("Detected Anomalies", f"{len(anomalies):,}", 
           delta=f"{(len(anomalies)/len(df)*100):.1f}% from total", delta_color="inverse")
col3.metric("Total Exposure", f"${anomalies[target_col].sum():,.0f}")
col4.metric("Avg Risk Score", f"{df['Final_Score'].mean():,.1f}")

st.divider()

# Row 2: Visualizations (Bergerak secara Real-Time)
c1, c2 = st.columns([6, 4])

with c1:
    st.subheader("Transaction Risk Distribution")
    # Menggunakan target_col agar fleksibel dengan file apa pun
    fig = px.scatter(df, x=df.index, y=target_col, color="Final_Score", 
                     size=target_col, color_continuous_scale='RdYlGn_r',
                     title="Visualizing Risks over Time")
    # Tambahkan garis ambang batas (Threshold Line) agar visual lebih presisi
    fig.add_hline(y=(risk_threshold/100)*max_val, line_dash="dash", line_color="red", annotation_text="Threshold")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("Risk Category Breakdown")
    
    # LOGIKA BINS YANG LEBIH STABIL
    # Kita pastikan urutannya selalu naik: 0 -> 40 -> Threshold -> 100
    # Jika threshold di bawah 40, kita sesuaikan agar tidak Error
    low_limit = 40
    current_threshold = risk_threshold
    
    if current_threshold <= low_limit:
        bins = [0, current_threshold, (current_threshold + 100)/2, 100]
    else:
        bins = [0, low_limit, current_threshold, 100]
    
    # Menghitung distribusi risiko
    risk_labels = ['Low', 'Medium', 'High']
    df['Risk_Category'] = pd.cut(df['Final_Score'], bins=bins, labels=risk_labels, include_lowest=True)
    risk_counts = df['Risk_Category'].value_counts().reset_index()
    risk_counts.columns = ['Category', 'Count']

    # Pie Chart dengan Plotly Express
    fig_pie = px.pie(
        risk_counts, 
        values='Count', 
        names='Category',
        color='Category',
        color_discrete_map={'High':'#ef553b', 'Medium':'#fecb52', 'Low':'#00cc96'},
        hole=0.4 # Membuatnya jadi Donut Chart agar lebih modern
    )
    
    fig_pie.update_traces(textposition='inside', textinfo='percent+label')
    fig_pie.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
    
    st.plotly_chart(fig_pie, use_container_width=True)

# Row 3: Investigation Table (Berubah Real-Time)
st.subheader("🚩 Anomaly Investigation List")
st.dataframe(anomalies.sort_values(by='Final_Score', ascending=False), use_container_width=True)

# --- 3. EXPORT TO EXCEL ---
def to_excel(df_export):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_export.to_excel(writer, index=False, sheet_name='Audit_Report')
        workbook  = writer.book
        worksheet = writer.sheets['Audit_Report']
        header_fmt = workbook.add_format({'bold': True, 'fg_color': '#1F4E78', 'font_color': 'white', 'border': 1})
        for col_num, value in enumerate(df_export.columns.values):
            worksheet.write(0, col_num, value, header_fmt)
    return output.getvalue()

if not anomalies.empty:
    st.download_button(
        label="📥 Download Audit Report (Excel)",
        data=to_excel(anomalies),
        file_name="Audit_Anomaly_Report_saidUhuud.xlsx",
        mime="application/vnd.ms-excel"
    )

st.sidebar.success("App Status: Ready for Audit")








