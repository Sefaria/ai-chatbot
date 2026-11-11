from rest_framework import serializers


class SegmentSerializer(serializers.Serializer):
    seq = serializers.IntegerField(min_value=1)
    text = serializers.CharField(max_length=2000)
    page_url = serializers.URLField(max_length=2000, required=False, default="")


class AnalyzeSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    segments = SegmentSerializer(many=True, allow_empty=True, max_length=30)


class SuggestionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["dismissed", "accepted"])
