import subprocess
import sys

def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

# Coba instal otomatis jika belum ada
try:
    import sqlalchemy
    import psycopg2
except ImportError:
    print("📦 Library tidak ditemukan. Sedang menginstal sqlalchemy dan psycopg2...")
    install('sqlalchemy')
    install('psycopg2-binary')
    print("✅ Instalasi selesai!")

import pandas as pd
from sqlalchemy import create_engine

import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import random

# 1. KONFIGURASI KONEKSI
PASSWORD = '{PASSWORD}' 
DB_URL = f"postgresql://postgres:{PASSWORD}@localhost:5432/audit_intelligence_db"
engine = create_engine(DB_URL)

print("⏳ Sedang menghasilkan 50.000 data transaksi...")

# 2. GENERATE DATA DUMMY
np.random.seed(42)
n_rows = 50000

data = {
    'transaction_id': [f'TRX-{i:05d}' for i in range(n_rows)],
    'date': pd.date_range(start='2025-01-01', periods=n_rows, freq='min'),
    'vendor_id': [f'VND-{np.random.randint(100, 200)}' for _ in range(n_rows)],
    'employee_id': [f'EMP-{np.random.randint(10, 50)}' for _ in range(n_rows)],
    'amount': np.random.uniform(100, 10000, n_rows).round(2),
    'category': np.random.choice(['Office', 'Travel', 'IT', 'Marketing', 'Consulting'], n_rows)
}

df = pd.DataFrame(data)

# 3. SISIPKAN POLA FRAUD (Transaction Splitting)
#buat satu vendor menerima banyak transaksi kecil di bawah batas audit
fraud_indices = random.sample(range(n_rows), 10)
for idx in fraud_indices:
    df.loc[idx, 'amount'] = 995.00
    df.loc[idx, 'vendor_id'] = 'VND-999'

# 4. KIRIM KE POSTGRESQL
try:
    df.to_sql('transactions', engine, if_exists='replace', index=False)
    print("✅ Berhasil! 50.000 data telah masuk ke database 'audit_intelligence_db'.")
except Exception as e:
    print(f"❌ Gagal: {e}")