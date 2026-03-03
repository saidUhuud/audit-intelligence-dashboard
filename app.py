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
    
    st.divider()
    st.subheader("1. Data Sample")

    # --- BAGIAN SAMPLE DATA (XLSXWRITER) ---
    @st.cache_data
    def generate_large_sample():
        np.random.seed(42)
        data_sample = {
            'Date': pd.date_range(start='2025-01-01', periods=1500, freq='H'),
            'Vendor': np.random.choice(['Alpha Tech', 'Beta Corp', 'Global Solutions', 'Delta Industry', 'Indo Prima'], 1500),
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
        label="📥 Download 1,500 Rows Sample (Excel)",
        data=get_xlsx_sample(sample_df),
        file_name="audit_sample_saiduhuud.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.caption("No data? Download this sample to test the app")
    
    st.divider()
    st
