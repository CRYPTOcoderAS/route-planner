from rest_framework import serializers


class TripRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=120, help_text="'City, ST' or 'lat,lon'")
    finish = serializers.CharField(max_length=120, help_text="'City, ST' or 'lat,lon'")
    start_fuel_gallons = serializers.FloatField(
        required=False, min_value=0, max_value=50,
        help_text="Fuel in the tank at departure (default: full, 50 gal).",
    )
    mode = serializers.ChoiceField(
        choices=["balanced", "cheapest"], default="balanced",
        help_text="cheapest = absolute minimum fuel cost; balanced = near-minimum cost with far fewer stops.",
    )

    def validate(self, attrs):
        if attrs["start"].strip().lower() == attrs["finish"].strip().lower():
            raise serializers.ValidationError("start and finish must differ.")
        return attrs
