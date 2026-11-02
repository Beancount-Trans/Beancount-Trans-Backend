# project/apps/translate/urls.py
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from project.apps.translate.views import views

urlpatterns = [
    # path('trans', views.BillAnalyzeView.as_view(), name='trans'),
    path('trans', views.SingleBillAnalyzeView.as_view(), name='trans'),
    path('bill-support', views.BillSupportStatusView.as_view(), name='bill_support'),
    path('multi', views.MultiBillAnalyzeView.as_view(), name='multi'),
    path('upload-parse', views.UploadParseReviewView.as_view(), name='upload_parse'),
    path('write-collect', views.WriteCollectView.as_view(), name='write_collect'),
    path('task_group_status', views.TaskGroupStatusView.as_view(), name='task_group_status'),
    path('parse-task-status', views.ParseTaskStatusView.as_view(), name='parse_task_status'),
    path('reparse', views.ReparseEntryView.as_view(), name='reparse'),
    path('validate-entry', views.ValidateEntryView.as_view(), name='validate_entry'),
    path('cancel', views.CancelParseView.as_view(), name='cancel'),
    # 统一条目审核 API（用户级）
    path('entry-review/results', views.EntryReviewResultsView.as_view(), name='entry_review_results'),
    path('entry-review/reparse', views.EntryReviewReparseView.as_view(), name='entry_review_reparse'),
    path('entry-review/entries/<str:uuid>/edit', views.EntryReviewEditView.as_view(), name='entry_review_edit'),
    path('entry-review/entries/<str:uuid>/tags', views.EntryReviewTagsView.as_view(), name='entry_review_tags'),
    path('entry-review/preview-sync', views.EntryReviewPreviewSyncView.as_view(), name='entry_review_preview_sync'),
    path('entry-review/confirm', views.EntryReviewConfirmView.as_view(), name='entry_review_confirm'),
    path('entry-review/reparse-all', views.EntryReviewReparseAllView.as_view(), name='entry_review_reparse_all'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
