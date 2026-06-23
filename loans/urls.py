from django.urls import path
from . import views

urlpatterns = [
    # Main Base Dashboard Entry
    path('', views.index, name='index'),

    # AJAX Fragments Engine Endpoints 
    
    path('panel/view-master/', views.panel_view_master, name='panel_view_master'),
    path('panel/compare/', views.panel_compare, name='panel_compare'),
    path('panel/kibor/', views.panel_kibor, name='panel_kibor'),
    path('panel/add-yearly-kibor/', views.panel_add_yearly_kibor, name='panel_add_yearly_kibor'),
    path('panel/fetch-obi/', views.panel_fetch_obi, name='panel_fetch_obi'),
    path('api/set-period/', views.set_period, name='set_period'),

    # Core Action Routes
    path('view-initial/', views.view_initial, name='view_initial'),
    path('remove-initial/', views.remove_initial, name='remove_initial'),
    path('remove-file/', views.remove_file, name='remove_file'),
    path('upload-initial/', views.upload_initial, name='upload_initial'),
    path('upload-new/', views.upload_new, name='upload_new'),
    path('compare/', views.compare_files, name='compare_files'),
    path('download-results/', views.download_results, name='download_results'),

    # API Backend Tasks Scrapers
    path('scrape/', views.start_scrape_route, name='start_scrape'),
    path('scrape/status/', views.scrape_status_route, name='scrape_status'),
    path('scrape/files/', views.scrape_files_route, name='scrape_files'),
    path('fetch-kibor/', views.fetch_kibor, name='fetch_kibor'),
    path('subsidy-claim/', views.add_subsidy_claim, name='add_subsidy_claim'),
    
    path('fetch-yearly-kibor/', views.fetch_yearly_kibor, name='fetch_yearly_kibor'),
]