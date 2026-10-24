from django.http import JsonResponse
from django.views.decorators.cache import cache_control
from django.shortcuts import render
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from planner.datasets import get_datasets
from planner.optimizer import InfeasibleRoute
from planner.routing import RoutingError
from planner.serializers import TripRequestSerializer
from planner.services import LocationError, TripRequest, plan_trip


class RoutePlanView(APIView):

    def get(self, request: Request) -> Response:
        return self._plan(request.query_params)

    def post(self, request: Request) -> Response:
        return self._plan(request.data)

    def _plan(self, data) -> Response:
        ser = TripRequestSerializer(data=data)
        ser.is_valid(raise_exception=True)
        try:
            result = plan_trip(TripRequest(**ser.validated_data))
        except LocationError as exc:
            return _error(status.HTTP_400_BAD_REQUEST, "invalid_location", str(exc))
        except InfeasibleRoute as exc:
            return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "no_fuel_in_range", str(exc))
        except RoutingError as exc:
            return _error(status.HTTP_502_BAD_GATEWAY, "routing_failed", str(exc))
        return Response(result)


def _error(code: int, kind: str, detail: str) -> Response:
    return Response({"error": kind, "detail": detail}, status=code)


def health(request):
    return JsonResponse({"status": "ok", "stations_loaded": len(get_datasets().stations)})


@cache_control(public=True, max_age=86400)
def place_suggestions(request):
    query = request.GET.get("q", "")[:80]
    try:
        limit = max(1, min(int(request.GET.get("limit", 8)), 20))
    except ValueError:
        limit = 8
    return JsonResponse({"query": query, "results": get_datasets().places.suggest(query, limit)})


def map_view(request):
    return render(request, "planner/map.html", {
        "start": request.GET.get("start", "New York, NY"),
        "finish": request.GET.get("finish", "Los Angeles, CA"),
    })
