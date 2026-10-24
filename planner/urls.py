from django.urls import path

from planner import views

urlpatterns = [
    path("api/v1/route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("api/v1/health/", views.health, name="health"),
    path("map/", views.map_view, name="map"),
    path("", views.map_view, name="home"),
]
