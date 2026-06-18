import os
import glob
from io import BytesIO
import pandas as pd
from datetime import datetime

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
    ensure_dirs,
    find_master_file,
    INITIAL_DIR
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
    latest = find_latest_upload()
    return render(request, 'loans/partials/add_yearly_kibor.html', {'new_exists': bool(latest)})


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
    DATA_DIR = "data/uploads"
    excel_files = glob.glob(os.path.join(DATA_DIR, "*.xlsx"))
    excel_files = [f for f in excel_files if "_with_kibor" not in f]

    if not excel_files:
        return JsonResponse({'ok': False, 'error': 'No Excel files found'}, status=404)

    latest_file = max(excel_files, key=os.path.getmtime)

    try:
        df = pd.read_excel(latest_file, engine="openpyxl")
    except Exception as e:
        return JsonResponse({'ok': False, 'error': f'Error reading Excel: {str(e)}'}, status=500)

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

    output_file = latest_file.replace(".xlsx", "_with_kibor.xlsx")

    try:
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
        return JsonResponse({"ok": True, "file": output_file})
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