import sys
import time

from confluent_kafka import Consumer, KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from worker.ingestors import handlers
from backend.settings import Settings

sys.stdout.reconfigure(line_buffering=True)

TOPIC = "telemetry"
GROUP_ID = "ingest-workers"
BATCH_SIZE = 500        # "up to 500 messages or 1 second"
BATCH_WINDOW_S = 1.0

def ensure_topic() -> None:
    admin = AdminClient({"bootstrap.servers": Settings.kafka_bootstrap})
    while True:
        try:
            future = admin.create_topics(
                [NewTopic(TOPIC, num_partitions=1, replication_factor=1)]
            )[TOPIC]
            future.result(timeout=10)
            print(f"[ingest] created topic {TOPIC}")
            return
        except KafkaException as exc:
            code = exc.args[0].code() if exc.args else None
            if code == KafkaError.TOPIC_ALREADY_EXISTS:
                print(f"[ingest] topic {TOPIC} already exists")
                return
            print(f"[ingest] broker not ready ({exc}); retrying in 2s")
            time.sleep(2)
        except TimeoutError:
            print(f"[ingest] broker timed out; retrying in 2s")
            time.sleep(2)

def build_consumer() -> Consumer:
    conf = {
        "bootstrap.servers": Settings.kafka_bootstrap,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",   # brand-new group: start at message #0
        "enable.auto.commit": False,       # we commit BY HAND, after the DB work
    }
    return Consumer(conf)


def collect_batch(consumer: Consumer) -> list:
    """Poll until BATCH_SIZE messages or BATCH_WINDOW_S seconds elapsed."""
    deadline = time.time() + BATCH_WINDOW_S
    batch: list = []
    while len(batch) < BATCH_SIZE and time.time() < deadline:
        msg = consumer.poll(max(0.0, deadline - time.time()))
        if msg is None:
            continue                      # window expired, batch complete
        if msg.error():
            raise KafkaException(msg.error())
        batch.append(msg)
    return batch


def main() -> None:
    ensure_topic()
    consumer = build_consumer()
    consumer.subscribe([TOPIC])
    print(
        f"[ingest] connected to {Settings.kafka_bootstrap} | "
        f"topic={TOPIC} | group={GROUP_ID}"
    )
    total = 0
    try:
        while True:
            batch = collect_batch(consumer)
            if not batch:
                continue
            # 1) database work FIRST (raises on failure -> no commit ->
            #    the same batch is re-read after a restart: at-least-once)
            counts = handlers.handle_batch(batch)
            # 2) commit AFTER — now the broker may forget these messages
            consumer.commit(asynchronous=False)
            total += len(batch)
            print(
                f"[ingest] {len(batch)} msgs -> "
                f"run_start={counts['run_start']} "
                f"telemetry={counts['telemetry']} "
                f"run_end={counts['run_end']} "
                f"(total {total})"
            )
    except KeyboardInterrupt:
        print("\n[ingest] stopping")
    finally:
        consumer.close()   # leaves the group cleanly, commits nothing extra


if __name__ == "__main__":
    main()
