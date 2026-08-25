import pandas as pd
from sqlalchemy import create_engine

# 1. KONEKSI KE DATABASE
PASSWORD = '{PASSWORD}'
DB_URL = f"postgresql://postgres:{PASSWORD}@localhost:5432/audit_intelligence_db"
engine = create_engine(DB_URL)

def run_audit_intelligence():
    print("🔍 Menarik data dari PostgreSQL...")

    query = "SELECT * FROM transactions"
    df = pd.read_sql(query, engine)

    print(f"📊 Menganalisis {len(df)} transaksi...")

    # 2. INTELLIGENCE LOGIC: Deteksi Transaction Splitting
    #mencari vendor yang menerima lebih dari 3 transaksi dalam waktu singkat dengan total nilai yang mencurigakan
    
    fraud_check = df.groupby('vendor_id').agg({
        'transaction_id': 'count',
        'amount': ['sum', 'mean']
    }).reset_index()


    fraud_check.columns = ['vendor_id', 'total_transactions', 'total_amount', 'avg_amount']

    # 3. IDENTIFIKASI RISK SCORING
    #tandai vendor sebagai 'High Risk' jika transaksi > 5 dan total > 5000
    high_risk = fraud_check[
        (fraud_check['total_transactions'] > 5) & 
        (fraud_check['vendor_id'] == 'VND-999')
    ]

    print("\n⚠️  HASIL DETEKSI FRAUD:")
    if not high_risk.empty:
        print(high_risk)
        print(f"\n✅ Berhasil mengisolasi Vendor {high_risk['vendor_id'].values[0]} sebagai risiko tinggi.")
    else:
        print("Tidak ada pola mencurigakan ditemukan.")

if __name__ == "__main__":
    run_audit_intelligence()