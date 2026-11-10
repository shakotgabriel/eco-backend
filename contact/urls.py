from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ContactMessageViewSet, submit_contact_form

router = DefaultRouter()
router.register("messages", ContactMessageViewSet, basename="contact-message")

urlpatterns = [
    path("", submit_contact_form, name="contact_form"),
] + router.urls
