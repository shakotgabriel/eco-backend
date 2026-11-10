from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import TicketViewSet, board_ticket, ticket_pdf_public, ticket_qr_public, verify_ticket_qr

router = DefaultRouter()
router.register("", TicketViewSet)

urlpatterns = [
    path("verify-qr/", verify_ticket_qr, name="verify_ticket_qr"),
    path("board/", board_ticket, name="board_ticket"),
    path("qr/<str:ticket_number>/", ticket_qr_public, name="ticket_qr_public"),
    path("pdf/<str:ticket_number>/", ticket_pdf_public, name="ticket_pdf_public"),
] + router.urls
