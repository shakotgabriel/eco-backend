from bebeto_backend.base import BaseModel
from django.db import models


class City(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=3, unique=True, help_text="3-letter city code, e.g. JUB, NBO, KLA")
    country = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "cities"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}, {self.country}"
