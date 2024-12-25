from django.urls import path
from . import views

urlpatterns = [
    path('autopoiesis', views.autopoiesis, name='autopoiesis'),
]
