import os
import pandas as pd
#from settings import ALLOWED_EXTENSIONS, UPLOAD_DIR, INITIAL_DIR, MASTER_DIR

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}
UPLOAD_DIR = "data/uploads"
INITIAL_DIR = "data/initial"
MASTER_DIR = "data/master"

# 1. Validation and Directory setup
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def ensure_dirs():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(INITIAL_DIR, exist_ok=True)
    os.makedirs(MASTER_DIR, exist_ok=True)


# 2. Key Identifier Matching (Account ID or CNIC)
def find_key_column(df):
    cols = [c.lower() for c in df.columns]
    if any("account" in c for c in cols):
        for c in df.columns:
            if "account" in c.lower():
                return c
    if any("cnic" in c for c in cols):
        for c in df.columns:
            if "cnic" in c.lower():
                return c
    return None


# 3. Data Processing & Loading
def load_dataframe(path):
    if path.lower().endswith('.csv'):
        return pd.read_csv(path, dtype=str)
    else:
        return pd.read_excel(path, dtype=str, engine='openpyxl')


# 4. Sheet Comparison Engine Matrix
def compare_data(initial_df, new_df):
    key_col_init = find_key_column(initial_df)
    key_col_new = find_key_column(new_df)

    if key_col_init is None or key_col_new is None:
        raise ValueError('Key column not found (Account ID or CNIC) in one of the files')

    initial = initial_df.copy()
    new = new_df.copy()
    initial = initial.rename(columns={key_col_init: 'KEY'})
    new = new.rename(columns={key_col_new: 'KEY'})

    initial['KEY'] = initial['KEY'].astype(str).str.strip()
    new['KEY'] = new['KEY'].astype(str).str.strip()

    initial_indexed = initial.set_index('KEY')
    new_indexed = new.set_index('KEY')

    all_keys = new_indexed.index.unique()
    results = []

    for k in all_keys:
        entry = {'key': k, 'status': 'ok', 'mismatches': []}
        if k not in initial_indexed.index:
            entry['status'] = 'missing_in_initial'
            results.append(entry)
            continue

        init_row = initial_indexed.loc[k]
        new_row = new_indexed.loc[k]

        if isinstance(init_row, pd.DataFrame):
            init_row = init_row.iloc[0]
        if isinstance(new_row, pd.DataFrame):
            new_row = new_row.iloc[0]

        common_cols = set(initial.columns) & set(new.columns)
        common_cols.discard('KEY')

        for col in sorted(common_cols):
            v1 = '' if pd.isna(init_row.get(col, '')) else str(init_row.get(col, '')).strip()
            v2 = '' if pd.isna(new_row.get(col, '')) else str(new_row.get(col, '')).strip()
            if v1 != v2:
                entry['mismatches'].append({'column': col, 'initial': v1, 'new': v2})

        if entry['mismatches']:
            entry['status'] = 'mismatch'

        results.append(entry)

    return results


# 5. File Discovery Helpers
def find_latest_upload():
    candidates = []
    if os.path.isdir(UPLOAD_DIR):
        for f in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(p):
                candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def find_master_file():
    if os.path.isdir(MASTER_DIR):
        for f in os.listdir(MASTER_DIR):
            p = os.path.join(MASTER_DIR, f)
            if os.path.isfile(p):
                return p
    return None