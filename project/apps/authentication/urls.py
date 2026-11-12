from django.urls import path, include
from rest_framework.routers import DefaultRouter
from project.apps.authentication.views import (
    PhoneAuthViewSet,
    UsernameAuthViewSet,
    AccountBindingViewSet,
    UserProfileViewSet,
    TwoFactorAuthViewSet,
    AuthPublicConfigAPIView,
    PersonalAccessTokenViewSet,
)

router = DefaultRouter()
router.register(r'phone', PhoneAuthViewSet, basename='phone-auth')
router.register(r'username', UsernameAuthViewSet, basename='username-auth')
router.register(r'bindings', AccountBindingViewSet, basename='account-binding')
router.register(r'profile', UserProfileViewSet, basename='user-profile')
router.register(r'2fa', TwoFactorAuthViewSet, basename='2fa')
router.register(r'tokens', PersonalAccessTokenViewSet, basename='personal-access-token')

urlpatterns = [
    path('public-config/', AuthPublicConfigAPIView.as_view(), name='auth-public-config'),
    path('', include(router.urls)),
]

