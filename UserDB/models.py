from django.db import models
from django.db import models
from django.conf import settings


class Verified(models.Model):
    class SchoolClass(models.TextChoices):
        # 8-ми клас
        CLASS_8A = '8A', '8А клас'
        CLASS_8B = '8B', '8Б клас'
        CLASS_8V = '8V', '8В клас'
        # 9-ти клас
        CLASS_9A = '9A', '9А клас'
        CLASS_9B = '9B', '9Б клас'
        CLASS_9V = '9V', '9В клас'
        # 10-ти клас
        CLASS_10A = '10A', '10А клас'
        CLASS_10B = '10B', '10Б клас'
        CLASS_10V = '10V', '10В клас'
        # 11-ти клас
        CLASS_11A = '11A', '11А клас'
        CLASS_11B = '11B', '11Б клас'
        CLASS_11V = '11V', '11В клас'
        # 12-ти клас
        CLASS_12A = '12A', '12А клас'
        CLASS_12B = '12B', '12Б клас'
        CLASS_12V = '12V', '12В клас'

        U = 'U', 'Учител'

    first_name = models.CharField(max_length=100, verbose_name="име")
    last_name = models.CharField(max_length=100, verbose_name="Фамилия")
    clas = models.CharField(max_length=3, choices=SchoolClass.choices, verbose_name="клас" )
    code = models.CharField(max_length=6, verbose_name="код за достъп", unique=True)
    def __str__(self):
    	return f'{self.clas}: {self.first_name} {self.last_name}'

class UserData(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, verbose_name="Потребител", null=True, blank=True)
    verify = models.OneToOneField(Verified, on_delete=models.CASCADE, verbose_name="Верифициран профил")

