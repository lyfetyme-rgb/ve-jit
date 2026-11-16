import json
from kafka import KafkaProducer
from kafka.errors import KafkaError
from app.config import settings


class KafkaEventPublisher:
    def __init__(self) -> None:
        self._producer = None

    def _ensure_producer(self):
        if self._producer is None:
            self._producer = KafkaProducer(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
        return self._producer

    def publish(self, topic: str, payload: dict) -> dict:
        try:
            producer = self._ensure_producer()
            producer.send(topic, payload)
            producer.flush(timeout=2)
            return {"published": True, "topic": topic}
        except (KafkaError, OSError):
            return {"published": False, "topic": topic}
