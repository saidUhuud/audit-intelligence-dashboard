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
    /* Memperkecil padding dan font size metrik agar muat satu baris */
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

# --- 2. SIDEBAR (PROFESSIONAL IDENTITY & CONTROLS) ---
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

    sample_df = generate_large_sample(currency_choice)

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
        label=f"📥 Download {currency_choice} Sample",
        data=get_xlsx_sample(sample_df),
        file_name=f"audit_sample_{currency_choice.split()[0]}_saidUhuud.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    
    st.divider()
    st.subheader("2. Upload & Settings")
    uploaded_file = st.file_uploader("Upload Raw Data (CSV or Excel)", type=['csv', 'xlsx'])
    
    risk_threshold = st.slider("Select Risk Threshold (%)", 0, 100, 70)

# --- 3. DATA LOADING ENGINE ---
def load_data(file):
    if file is not None:
        try:
            if file.name.endswith('.csv'):
                df_loaded = pd.read_csv(file, sep=None, engine='python')
            else:
                df_loaded = pd.read_excel(file)
            
            money_keywords = ['amount', 'nilai', 'total', 'harga', 'price', 'nominal']
            for col in df_loaded.columns:
                is_money_col = any(key in col.lower() for key in money_keywords)
                if is_money_col and df_loaded[col].dtype == 'object':
                    df_loaded[col] = df_loaded[col].astype(str).str.replace(r'[Rp\s.]', '', regex=True).str.replace(',', '.')
                    df_loaded[col] = pd.to_numeric(df_loaded[col], errors='coerce')
            return df_loaded
        except Exception as e:
            st.error(f"Error reading file: {e}")
            return pd.DataFrame()
    return generate_large_sample("Rupiah (IDR)")

df = load_data(uploaded_file)

# --- 4. ANALYTICS ENGINE (PERBAIKAN ERROR 0 & 100) ---
if not df.empty:
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    target_col = "Amount" 
    for col in numeric_cols:
        if any(x in col.lower() for x in ['amount', 'nilai', 'total', 'harga', 'price']):
            target_col = col
            break
    
    is_rupiah = df[target_col].mean() > 100000

    # Risk Scoring
    max_val = df[target_col].max() if df[target_col].max() > 0 else 1
    df['Risk_Score'] = (df[target_col] / max_val * 100).round(2)
    df['Is_Round'] = df[target_col].apply(lambda x: 1 if x % 100 == 0 else 0)
    df['Final_Score'] = (df['Risk_Score'] + (df['Is_Round'] * 20)).clip(0, 100)
    
    # PERBAIKAN BINS: Menghindari duplikasi bins agar tidak Error 
    low_limit = 40
    # Menggunakan set & sorted agar bins selalu unik dan berurutan
    bins_edges = sorted(list(set([0, low_limit, risk_threshold, 100])))
    
    # Jika bins_edges tidak unik (misal threshold diset 0 atau 40), kita buat label dinamis
    labels_dynamic = ['Low', 'Medium', 'Critical']
    if len(bins_edges) == 3: labels_dynamic = ['Low', 'Critical']
    if len(bins_edges) == 2: labels_dynamic = ['Risk']

    df['Risk_Level'] = pd.cut(df['Final_Score'], bins=bins_edges, labels=labels_dynamic, include_lowest=True)
    
    # Filtering Anomalies - SINKRON DENGAN TABEL
    anomalies = df[df['Final_Score'] >= risk_threshold].copy()

# --- 5. DASHBOARD UI ---
st.title("🛡️ AUDIT INTELLIGENCE CORE SYSTEMS")
st.warning("👈 **Open sidebar for upload and threshold settings**")

# Row 1: Key Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Transactions", f"{len(df):,}")
col2.metric("Detected Anomalies", f"{len(anomalies):,}", 
           delta=f"{(len(anomalies)/len(df)*100):.1f}% of total", delta_color="inverse")

with col3:
    total_val = anomalies[target_col].sum()
    st.metric(f"Total Exposure ({'IDR' if is_rupiah else 'USD'})", 
              f"{'Rp ' if is_rupiah else '$'}{total_val:,.0f}")

with col4:
    # Metrik Real-time
    current_avg_risk = anomalies['Final_Score'].mean() if not anomalies.empty else 0
    st.metric("Avg Risk Score", f"{current_avg_risk:,.1f}")
    st.caption(f"Mode: **{'IDR' if is_rupiah else 'USD'}**")

st.divider()

# Row 2: Visualizations
c1, c2 = st.columns([6, 4])
with c1:
    st.subheader("Transaction Risk Distribution")
    fig = px.scatter(df, x=df.index, y=target_col, color="Final_Score", 
                     size=target_col, color_continuous_scale='RdYlGn_r')
    fig.add_hline(y=(risk_threshold/100)*max_val, line_dash="dash", line_color="red")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("Risk Category Breakdown")
    risk_counts = df['Risk_Level'].value_counts().reset_index()
    risk_counts.columns = ['Category', 'Count']
    fig_pie = px.pie(risk_counts, values='Count', names='Category', hole=0.4,
                     color='Category', color_discrete_map={'Critical':'#ef553b', 'Medium':'#fecb52', 'Low':'#00cc96'})
    st.plotly_chart(fig_pie, use_container_width=True)

# --- 5. DASHBOARD UI (BAGIAN TABEL INVESTIGASI) ---

st.subheader("🚩 Anomaly Investigation List")

# Memastikan tabel mengambil data 'anomalies' yang baru saja dihitung di Analytics Engine
if not anomalies.empty:
    # Menggunakan container untuk memastikan UI refresh secara bersih
    with st.container():
        st.dataframe(
            anomalies.sort_values(by='Final_Score', ascending=False), 
            use_container_width=True,
            hide_index=True
        )
else:
    st.info(f"No transactions found with Risk Score above {risk_threshold}%. Try lowering the threshold.")

# --- 6. EXPORT ---
if not anomalies.empty:
    def to_excel(df_export):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_export.sort_values(by='Final_Score', ascending=False).to_excel(writer, index=False)
        return output.getvalue()

    st.download_button(label="📥 Download Audit Report", data=to_excel(anomalies), file_name="Audit_Report_saidUhuud.xlsx")

st.sidebar.success("App Status: Ready for Audit")

