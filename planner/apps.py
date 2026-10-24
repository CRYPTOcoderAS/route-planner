from django.apps import AppConfig


class PlannerConfig(AppConfig):
    name = "planner"

    def ready(self) -> None:
        from planner.datasets import get_datasets

        get_datasets()
