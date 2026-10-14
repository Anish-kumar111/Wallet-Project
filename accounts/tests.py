from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

PASSWORD = "S3cure-pass-123"


class AuthTests(APITestCase):
    def test_register_user_and_provider_roles(self):
        for payload in [
            {"username": "alice", "role": "USER"},
            {"username": "dr_bob", "role": "PROVIDER", "per_minute_rate": "50.00"},
        ]:
            role = payload["role"]
            res = self.client.post(reverse("register"), {**payload, "password": PASSWORD}, format="json")
            self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
            self.assertEqual(res.data["role"], role)
            self.assertNotIn("password", res.data)

        self.assertTrue(User.objects.get(username="alice").is_consumer)
        self.assertTrue(User.objects.get(username="dr_bob").is_provider)

    def test_role_defaults_to_user(self):
        res = self.client.post(reverse("register"), {"username": "carol", "password": PASSWORD}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["role"], "USER")

    def test_invalid_role_rejected(self):
        res = self.client.post(
            reverse("register"), {"username": "eve", "password": PASSWORD, "role": "ADMIN"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_provider_requires_rate_and_user_cannot_have_one(self):
        res = self.client.post(
            reverse("register"), {"username": "p", "password": PASSWORD, "role": "PROVIDER"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("per_minute_rate", res.data)

        res = self.client.post(
            reverse("register"),
            {"username": "u", "password": PASSWORD, "role": "USER", "per_minute_rate": "10.00"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_provider_can_update_rate(self):
        provider = User.objects.create_user(username="p", password=PASSWORD, role="PROVIDER", per_minute_rate=50)
        self.client.force_authenticate(provider)
        res = self.client.patch(reverse("me"), {"per_minute_rate": "75.00"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        provider.refresh_from_db()
        self.assertEqual(str(provider.per_minute_rate), "75.00")

    def test_obtain_jwt_and_access_me(self):
        User.objects.create_user(username="alice", password=PASSWORD, role="USER")

        res = self.client.post(reverse("token-obtain"), {"username": "alice", "password": PASSWORD}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        me = self.client.get(reverse("me"))
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data["username"], "alice")
        self.assertEqual(me.data["role"], "USER")

    def test_me_requires_authentication(self):
        res = self.client.get(reverse("me"))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
