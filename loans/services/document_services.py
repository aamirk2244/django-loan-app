import os
import glob
import pandas as pd
#from settings import ALLOWED_EXTENSIONS, UPLOAD_DIR, INITIAL_DIR, MASTER_DIR

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}
UPLOAD_DIR = "data/uploads"
INITIAL_DIR = "data/initial"
MASTER_DIR = "data/master-kibor"

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
    # If the path is a directory, find the latest file inside it
    if os.path.isdir(path):
        # Find all .xlsx and .csv files
        files = glob.glob(os.path.join(path, "*.xlsx")) + glob.glob(os.path.join(path, "*.csv"))
        if not files:
            raise FileNotFoundError(f"No Excel or CSV files found in directory: {path}")
        # Target the latest file based on modification time
        path = max(files, key=os.path.getmtime)

    # Load the file based on its extension
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

def merge_oas_amounts_to_master(master_df, new_df):
    """
    Finds matching entries via 'Customer Identification No', extracts OAS columns 
    (M1, M2, M3), and merges them into the master dataframe.
    """
    ID_COL = 'Customer Identification No'
    OAS_COLS = ['OAS Amount M1', 'OAS Amount M2', 'OAS Amount M3']
    
    # 1. Validate that the identifier column exists in both dataframes
    if ID_COL not in master_df.columns:
        raise ValueError(f"Master file is missing the required identifier column: '{ID_COL}'")
    if ID_COL not in new_df.columns:
        raise ValueError(f"New data file is missing the required identifier column: '{ID_COL}'")
        
    # 2. Validate that at least one of the target OAS columns exists in the new file
    available_oas_cols = [col for col in OAS_COLS if col in new_df.columns]
    if not available_oas_cols:
        raise ValueError(f"New file does not contain any of the expected OAS columns: {OAS_COLS}")

    # 3. Create clean working copies and enforce string/stripped keys for perfect matching
    master = master_df.copy()
    new = new_df.copy()
    
    master[ID_COL] = master[ID_COL].astype(str).str.strip()
    new[ID_COL] = new[ID_COL].astype(str).str.strip()
    
    # 4. Filter the new dataframe to keep only the ID and the available OAS columns
    # Dropping duplicates ensures we don't accidentally bloat the master dataframe on merge
    new_subset = new[[ID_COL] + available_oas_cols].drop_duplicates(subset=[ID_COL])
    
    # 5. Initialize the OAS columns in master if they don't already exist
    for col in available_oas_cols:
        if col not in master.columns:
            master[col] = pd.NA

    # 6. Set index to perform an in-place update for existing rows
    master.set_index(ID_COL, inplace=True)
    new_subset.set_index(ID_COL, inplace=True)
    
    # Update master with values from the new sheet where the IDs match
    master.update(new_subset)
    
    # 7. (Optional) If there are new IDs in the update file that don't exist in master, 
    # you can combine them. If you ONLY want to update existing master entries, skip this step.
    missing_ids = new_subset.index.difference(master.index)
    if not missing_ids.empty:
        new_entries = new_subset.loc[missing_ids]
        master = pd.concat([master, new_entries], axis=0)

    # Reset index back to a standard column before returning
    return master.reset_index()
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


def find_latest_upload_with_args(directory):
    candidates = []
    if os.path.isdir(directory):
        for f in os.listdir(directory):
            p = os.path.join(directory, f)
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