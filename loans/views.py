import os
import glob
from pathlib import Path
from io import BytesIO
import pandas as pd
from datetime import datetime
import shutil
from django.shortcuts import render, redirect
from django.http import JsonResponse, FileResponse, Http404
from django.contrib import messages
from django.urls import reverse
from django.conf import settings

# Importing your existing python business logic services unchanged
# Change this:
# from services import start_scrape, scrape_status, list_files, ...

# To this:
from .services.document_services import (
    allowed_file,
    find_latest_upload,
    load_dataframe,
    compare_data,
    merge_oas_amounts_to_master,
    find_latest_upload_with_args,
    ensure_dirs,
    find_master_file,
    INITIAL_DIR,
    MASTER_DIR
)

from .services.oas_scraper import (
    STATEMENTS_DIR,
    OAS_AMOUNT_DIR,
    merge_oas_amounts_to_master,
    extract_statement_data,
    parse_statement_end_date,
    extract_text_from_pdf,
    extract_account_number,
    pick_latest_per_account,
    save_to_excel
)

from .services.scraper_services import start_scrape, scrape_status, list_files

# Keep your private helper utility function
def _get_initial_path_and_name():
    initial_path = None
    initial_name = None
    if os.path.isdir(INITIAL_DIR):
        for f in os.listdir(INITIAL_DIR):
            initial_path = os.path.join(INITIAL_DIR, f)
            initial_name = f
            break
    return initial_path, initial_name

def _get_master_kibor_path_and_name():
    master_kibor_path = None
    master_kibor_name = None
    if os.path.isdir(MASTER_DIR):
        for f in os.listdir(MASTER_DIR):
            master_kibor_path = os.path.join(MASTER_DIR, f)
            master_kibor_name = f
            break
    return master_kibor_path, master_kibor_name

# 1. Main Dashboard Route  
def index(request):
    initial_filename = None
    initial_mtime = None
    initial_path, initial_filename = _get_initial_path_and_name()
    if initial_path:
        try:
            initial_mtime = pd.to_datetime(os.path.getmtime(initial_path), unit='s')
        except Exception:
            initial_mtime = None

    initial_exists = initial_filename is not None
   
    latest = find_latest_upload()
    new_filename = os.path.basename(latest) if latest else None
    new_mtime = None
    if latest:
        try:
            new_mtime = pd.to_datetime(os.path.getmtime(latest), unit='s')
        except Exception:
            new_mtime = None

    master_path = find_master_file()
    master_filename = os.path.basename(master_path) if master_path else None
    master_mtime = None
    if master_path:
        try:
            master_mtime = pd.to_datetime(os.path.getmtime(master_path), unit='s')
        except Exception:
            master_mtime = None

    context = {
        'initial_exists': initial_exists,
        'initial_filename': initial_filename,
        'initial_mtime': initial_mtime,
        'new_exists': bool(latest),
        'new_filename': new_filename,
        'new_mtime': new_mtime,
        'master_exists': bool(master_path),
        'master_filename': master_filename,
        'master_mtime': master_mtime
    }
    return render(request, 'loans/index.html', context)



import pandas as pd
from django.core.cache import cache
from django.contrib import messages
from django.shortcuts import render, redirect

def panel_view_master(request):
    master_kibor_path, _ = _get_master_kibor_path_and_name()

    
    if not master_kibor_path:
        messages.error(request, 'No master kibor file found. Please Generate Yearly Kobor first.')
        return redirect('index')

    new_path = master_kibor_path

    # Create a unique cache key combining file path properties or timestamps
    # This ensures if a new file is uploaded, the cache updates automatically
    cache_key = f"master_df_{hash(new_path)}"
    
    # Try fetching previously formatted data from memory cache
    cached_results = cache.get(cache_key)
    
    if cached_results is not None:
        print("--- Loaded Master File directly from Cache! ---")
        return render(request, 'loans/view_master.html', {'results': cached_results})

    # Cache miss -> Read from disk
    try:
        print("--- Cache Miss: Reading Master Excel File from Disk... ---")
        # If you still need initial_df for calculations, load it here:
        # initial_df = load_dataframe(master_kibor_path)
        
        results_df = load_dataframe(new_path)
        
        # Clean up NaN / Null spaces using numpy safely
        import numpy as np
        results_df = results_df.replace({np.nan: None})
        
        # Convert DataFrame to a standard list of serializable row dicts
        results_records = results_df.to_dict(orient='records')
        
        # Save records to Django cache for 10 minutes (600 seconds)
        cache.set(cache_key, results_records, timeout=600)
        
    except Exception as e:
        messages.error(request, f'Error during File Read: {str(e)}')
        return redirect('index')

    return render(request, 'loans/view_master.html', {'results': results_records})


# 2. AJAX Partial Views (Required for the dynamic right-side container updates)
def panel_compare(request):
    # Fetch data context needed for rendering the sub-elements within compare layout
    initial_path, initial_filename = _get_initial_path_and_name()
    latest = find_latest_upload()
    
    context = {
        'initial_exists': initial_filename is not None,
        'initial_filename': initial_filename,
        'new_exists': bool(latest),
        'new_filename': os.path.basename(latest) if latest else None,
    }
    return render(request, 'loans/partials/compare.html', context)

def panel_kibor(request):
    return render(request, 'loans/partials/kibor.html')

def panel_add_yearly_kibor(request):
    master_kibor_path, master_kibor_name = _get_master_kibor_path_and_name()
    master_kibor_exists = master_kibor_name is not None
    
    return render(request, 'loans/partials/add_yearly_kibor.html', { 'master_kibor_exists': master_kibor_exists, 'master_kibor_name': master_kibor_name , 'master_kibor_path': master_kibor_path })

# def panel_fetch_obi(request):
#     master_kibor_path, master_kibor_name = _get_master_kibor_path_and_name()
#     master_kibor_exists = master_kibor_name is not None
    
def panel_fetch_obi(request):
    """
    POST /api/process-oas/

    1. Delete all files in data/oas-amount/.
    2. Parse every *.pdf in data/sample-statements/.
    3. For each account, keep only the PDF with the latest statement period.
    4. Write results to data/oas-amount/oas_balances.xlsx.

    Response JSON:
        {
            "status": "success",
            "deleted_files": [...],
            "total_pdfs_scanned": N,
            "accounts_written": N,
            "output_file": "data/oas-amount/oas_balances.xlsx",
            "summary": [{"account": ..., "source_file": ..., "statement_end": ...}, ...],
            "errors": [...]
        }
    """
    OAS_AMOUNT_DIR.mkdir(parents=True, exist_ok=True)
    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1 – clear output directory
    deleted = []
    for f in OAS_AMOUNT_DIR.iterdir():
        if f.is_file():
            f.unlink()
            deleted.append(f.name)

    # Step 2 – parse all PDFs
    pdf_files = sorted(STATEMENTS_DIR.glob("*.pdf"))
    if not pdf_files:
        return JsonResponse(
            {"status": "error", "message": "No PDF files found in data/sample-statements/"},
            status=404,
        )

    records, errors = [], []
    for pdf_path in pdf_files:
        try:
            records.append(extract_statement_data(pdf_path))
        except Exception as exc:
            errors.append({"file": pdf_path.name, "error": str(exc)})

    if not records:
        return JsonResponse(
            {"status": "error", "message": "Failed to extract data from any PDF", "errors": errors},
            status=500,
        )

    # Step 3 – keep latest PDF per account
    final_records = pick_latest_per_account(records)

    # Step 4 – save Excel

    output_path = OAS_AMOUNT_DIR / "oas_balances.xlsx"
    save_to_excel(final_records, output_path)

    oas_amount_df = load_dataframe(OAS_AMOUNT_DIR)
    
    master_df = load_dataframe(MASTER_DIR)
    
    result = merge_oas_amounts_to_master(master_df, oas_amount_df)
    
    master_dir = Path("data/master-kibor")
    master_dir.mkdir(parents=True, exist_ok=True)
 
    for f in master_dir.iterdir():
        if f.is_file():
            f.unlink()
 
    output_path = master_dir / "master_kibore.xlsx"
    result.to_excel(output_path, index=False)
 

    # return JsonResponse({
    #     "status": "success",
    #     "deleted_files": deleted,
    #     "total_pdfs_scanned": len(pdf_files),
    #     "accounts_written": len(final_records),
    #     "output_file": str(output_path.relative_to(OAS_AMOUNT_DIR)),
    #     "summary": [
    #         {
    #             "account": r["account_number"],
    #             "source_file": r["filename"],
    #             "statement_end": r["statement_end"].strftime("%d-%b-%Y") if r["statement_end"] else "N/A",
    #         }
    #         for r in final_records
    #     ],
    #     "errors": errors,
    # })
    return redirect('index')

   
# 3. Initial Reference Operations
def view_initial(request):
    initial_path, initial_name = _get_initial_path_and_name()
    if not initial_path:
        messages.error(request, 'No initial file uploaded')
        return redirect('index')
    return FileResponse(open(initial_path, 'rb'), as_attachment=True, filename=initial_name)

def remove_initial(request):
    if request.method == 'POST':
        removed = False
        if os.path.isdir(INITIAL_DIR):
            for f in os.listdir(INITIAL_DIR):
                try:
                    os.remove(os.path.join(INITIAL_DIR, f))
                    removed = True
                except Exception:
                    pass
        if removed:
            messages.success(request, 'Initial reference removed')
        else:
            messages.error(request, 'No initial reference to remove')
    return redirect('index')

def remove_file(request):
    if request.method == 'POST':
        # Grab the file path from the URL parameters
        file_path = request.GET.get('file_path')
        
        if not file_path:
            messages.error(request, 'No file path provided.')
            return redirect('index')

        # Check if the file exists and delete it
        if os.path.exists(file_path) and os.path.isfile(file_path):
            try:
                os.remove(file_path)
                messages.success(request, 'Master Kibor removed successfully.')
            except Exception as e:
                messages.error(request, f'Error removing file: {str(e)}')
        else:
            messages.error(request, 'File does not exist or has already been removed.')
            
    return redirect('index')

def upload_initial(request):
    if request.method == 'POST':
        if 'file' not in request.FILES:
            messages.error(request, 'No file part')
            return redirect('index')
        file = request.FILES['file']
        if file.name == '':
            messages.error(request, 'No selected file')
            return redirect('index')
        if file and allowed_file(file.name):
            if os.path.isdir(INITIAL_DIR):
                for f in os.listdir(INITIAL_DIR):
                    try:
                        os.remove(os.path.join(INITIAL_DIR, f))
                    except Exception:
                        pass
            save_path = os.path.join(INITIAL_DIR, file.name)
            with open(save_path, 'wb+') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)
            messages.success(request, 'Initial file uploaded')
            return redirect('index')
        messages.error(request, 'Invalid file type')
    return redirect('index')


# 4. Sheet Comparison Operations
def upload_new(request):
    if request.method == 'POST':
        if 'file' not in request.FILES:
            messages.error(request, 'No file part')
            return redirect('index')
        file = request.FILES['file']
        if file.name == '':
            messages.error(request, 'No selected file')
            return redirect('index')
        if file and allowed_file(file.name):
            # In Django, media configurations or project settings root folders replace current_app.config
            upload_folder = getattr(settings, 'UPLOAD_FOLDER', 'data/uploads')
            save_path = os.path.join(upload_folder, file.name)
            with open(save_path, 'wb+') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)
            messages.success(request, 'Comparison file uploaded')
            return redirect('compare_files')
        messages.error(request, 'Invalid file type')
    return redirect('index')

def compare_files(request):
    initial_path, _ = _get_initial_path_and_name()
    new_path = find_latest_upload()

    if not initial_path:
        messages.error(request, 'No initial file uploaded. Please upload initial reference file first.')
        return redirect('index')
    if not new_path:
        messages.error(request, 'No comparison file uploaded. Please upload the file to compare.')
        return redirect('index')

    try:
        initial_df = load_dataframe(initial_path)
        new_df = load_dataframe(new_path)
        results = compare_data(initial_df, new_df)
        messages.success(request, 'Comparison completed successfully')
    except Exception as e:
        messages.error(request, f'Error during comparison: {str(e)}')
        return redirect('index')

    rows = []
    for r in results:
        if r['status'] != 'ok':
            if r['status'] == 'missing_in_initial':
                rows.append({'KEY': r['key'], 'status': r['status'], 'column': '', 'initial': '', 'new': ''})
            else:
                for m in r['mismatches']:
                    rows.append({'KEY': r['key'], 'status': r['status'], 'column': m['column'], 'initial': m['initial'], 'new': m['new']})

    csv_ready = bool(rows)
    issues_count = len(rows)

    return render(request, 'loans/results.html', {'results': results, 'csv_ready': csv_ready, 'issues_count': issues_count})

def download_results(request):
    initial_path, _ = _get_initial_path_and_name()
    new_path = find_latest_upload()
    if not initial_path or not new_path:
        messages.error(request, 'Missing files for download')
        return redirect('index')

    initial_df = load_dataframe(initial_path)
    new_df = load_dataframe(new_path)
    results = compare_data(initial_df, new_df)

    rows = []
    for r in results:
        if r['status'] != 'ok':
            if r['status'] == 'missing_in_initial':
                rows.append({'KEY': r['key'], 'status': r['status'], 'column': '', 'initial': '', 'new': ''})
            else:
                for m in r['mismatches']:
                    rows.append({'KEY': r['key'], 'status': r['status'], 'column': m['column'], 'initial': m['initial'], 'new': m['new']})

    buffer = BytesIO()
    pd.DataFrame(rows).to_csv(buffer, index=False)
    buffer.seek(0)
    return FileResponse(buffer, content_type='text/csv', as_attachment=True, filename='comparison_results.csv')


# 5. Core Scraping Control JSON Endpoints
def start_scrape_route(request):
    if request.method == 'POST':
        try:
            res = start_scrape()
            if res.get('status') == 'started':
                return JsonResponse({'ok': True, 'status': 'started'}, status=202)
            return JsonResponse({'ok': False, 'status': res.get('status')}, status=409)
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)

def scrape_status_route(request):
    try:
        return JsonResponse(scrape_status(), safe=False)
    except Exception as e:
        return JsonResponse({'running': False, 'error': str(e), 'log': []}, status=500)

def scrape_files_route(request):
    try:
        files = list_files()
        return JsonResponse({'count': len(files), 'files': files})
    except Exception as e:
        return JsonResponse({'count': 0, 'files': [], 'error': str(e)}, status=500)

def fetch_kibor(request):
    if request.method == 'POST':
        try:
            res = start_scrape()
            if res.get('status') == 'started':
                return JsonResponse({'ok': True, 'status': 'started'}, status=202)
            return JsonResponse({'ok': False, 'status': res.get('status')}, status=409)
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=500)


# 6. Processing & Computation Engine
def fetch_yearly_kibor(request):
    INITIAL_DIR = "data/initial"
    MASTER_DIR = "data/master-kibor"
    
    # 1. Grab the single Excel file
    excel_files = glob.glob(os.path.join(INITIAL_DIR, "*.xlsx"))
    
    if not excel_files:
        return JsonResponse({'ok': False, 'error': 'No Excel file found in initial directory'}, status=404)
    
    # Since there's only one, we just take the first item
    target_file = excel_files[0]

    # 2. Ensure destination folder exists
    os.makedirs(MASTER_DIR, exist_ok=True)
    
    # 3. Copy to master directory
    destination_path = os.path.join(MASTER_DIR, os.path.basename(target_file))
    shutil.copy2(target_file, destination_path)
    
    # 4. Load into dataframe
    df = pd.read_excel(destination_path, engine="openpyxl")

    df["M1 Kibor Jan 2026"] = None
    df["M2 Kibor Feb 2026"] = None
    df["M3 Kibor Mar 2026"] = None

    for idx, row in df.iterrows():
        disb_date = pd.to_datetime(row["Disb_Date"], errors="coerce")
        if pd.isna(disb_date):
            continue
        disb_month = disb_date.month
        disb_day = disb_date.day
        
        if disb_day <= 15:
            revision_month = disb_month - 1
            if revision_month == 0:
                revision_month = 12
        else:
            revision_month = disb_month
          
        def kibor_cal_for(_month, _year):
            revision_year = _year
            if _month <= revision_month:
                revision_year = revision_year - 1
            return get_yearly_kibor(revision_month, revision_year)
        
        df.at[idx, "M1 Kibor Jan 2026"] = kibor_cal_for(1, 2026)
        df.at[idx, "M2 Kibor Feb 2026"] = kibor_cal_for(2, 2026)
        df.at[idx, "M3 Kibor Mar 2026"] = kibor_cal_for(3, 2026)

    try:
        df.to_excel(destination_path, index=False, engine="openpyxl")
        return JsonResponse({"ok": True, "file": destination_path})
    except Exception as e:
        return JsonResponse({'running': False, 'error': str(e), 'log': []}, status=500)


# Cached internal calculation methods
_kibor_df = None

def _load_kibor_data():
    global _kibor_df
    if _kibor_df is not None:
        return _kibor_df
    
    KIBOR_CSV = "/home/aamir/Aamir-drive/aak-drive/Python-Project/static/data/kibor_summary.csv"
    df = pd.read_csv(KIBOR_CSV)
    df["1Year"] = pd.to_numeric(df["1Year"], errors="coerce")
    
    def extract_date(filename):
        try:
            date_str = filename.replace("Kibor-", "").replace(".pdf", "")
            return datetime.strptime(date_str, "%d-%b-%y")
        except:
            return pd.NaT
    
    df["kibor_date"] = df["filename"].apply(extract_date)
    _kibor_df = df
    return _kibor_df

def get_yearly_kibor(month, year):
    kibor_df = _load_kibor_data()
    kibor_value = kibor_df[
        (kibor_df["kibor_date"].dt.month == month) &
        (kibor_df["kibor_date"].dt.year == year)
    ]
    return kibor_value.iloc[0]["1Year"] if not kibor_value.empty else None