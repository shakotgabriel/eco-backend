from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


class LogoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="desk", password="pass1234", role="STAFF")

    def test_login_token_carries_role(self):
        response = self.client.post("/api/v1/auth/login/", {"username": "desk", "password": "pass1234"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RefreshToken(response.data["refresh"])["role"], "STAFF")

    def test_logout_blacklists_refresh_token(self):
        refresh = str(RefreshToken.for_user(self.user))

        response = self.client.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 205)

        reuse = self.client.post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(reuse.status_code, 401)

    def test_logout_tolerates_bad_token(self):
        response = self.client.post("/api/v1/auth/logout/", {"refresh": "garbage"}, format="json")
        self.assertEqual(response.status_code, 205)
