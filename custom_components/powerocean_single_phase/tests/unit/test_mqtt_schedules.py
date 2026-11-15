"""
test_mqtt_schedules: the scheduled tasks of the EcoFlow app over MQTT.

The frames in fixtures/mqtt_schedules.json were captured from the Android app
on 2026-11-15 (requests, and the replies of the system). The serial number is
replaced by SN_INVERTERBOX01 (same length).
"""

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.powerocean_single_phase.mqtt import (
    SCHEDULE_ADD,
    SCHEDULE_EDIT,
    MqttCallbacks,
    Part,
    PowerOceanMqtt,
    Schedule,
    pack_schedule_time,
    parse_schedule_list,
    parse_schedule_write_reply,
    protobuf_fields,
    schedule_delete_request,
    schedule_list,
    schedule_list_request,
    schedule_read_request,
    schedule_save_request,
    schedule_single,
    schedule_switch_request,
    split_message,
    unpack_schedule_time,
)

SN = "SN_INVERTERBOX01"
USER_ID = "1234567890"
CREDENTIALS = {
    "url": "mqtt.example.com",
    "port": "8883",
    "certificateAccount": "app-account",
    "certificatePassword": "app-password",
}
FRAMES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "mqtt_schedules.json").read_text()
)
SET_REPLY_TOPIC = f"/app/{USER_ID}/{SN}/thing/property/set_reply"
DEVICE_TOPIC = f"/app/device/property/{SN}"

CHARGE, USE_BATTERY = 1, 2


def frame(name: str) -> bytes:
    return bytes.fromhex(FRAMES[name])


def seq_of(name: str) -> int:
    ((_, header),) = protobuf_fields(frame(name))
    assert isinstance(header, bytes)
    seq = dict(protobuf_fields(header))[14]
    assert isinstance(seq, int)
    return seq


def content_of(name: str) -> bytes:
    (part,) = split_message(frame(name))
    return part.content


def hm(hour: int, minute: int = 0) -> int:
    """Return minutes since midnight."""
    return hour * 60 + minute


def schedule(**changes: int | bool) -> Schedule:
    """Return the schedule 12 of the capture (off, charge, 1500 W, 06:00-08:30)."""
    values: dict[str, int | bool] = {
        "schedule_id": 12,
        "schedule_type": CHARGE,
        "enabled": False,
        "power": 1500,
        "start": hm(6),
        "end": hm(8, 30),
        "repeat": 1,
        "days": 0,
    }
    return Schedule(**(values | changes))  # type: ignore[arg-type]


# -----------------------------
# Time of a schedule
# -----------------------------


def test_time_is_end_shifted_16_bits_plus_start() -> None:
    assert pack_schedule_time(hm(8, 30), hm(9)) == (hm(9) << 16) | hm(8, 30)
    assert unpack_schedule_time((hm(9) << 16) | hm(8, 30)) == (hm(8, 30), hm(9))


def test_time_may_run_over_midnight() -> None:
    packed = pack_schedule_time(hm(23), hm(2))

    assert packed == (120 << 16) | 1380
    assert unpack_schedule_time(packed) == (hm(23), hm(2))


# -----------------------------
# Requests, byte for byte as the app sent them
# -----------------------------

REQUESTS: list[tuple[str, Callable[[int], bytes]]] = [
    ("list_request", lambda seq: schedule_list_request(SN, seq)),
    ("read_request", lambda seq: schedule_read_request(SN, 12, CHARGE, seq)),
    ("switch_off_request", lambda seq: schedule_switch_request(SN, 12, 0, seq)),
    ("switch_on_request", lambda seq: schedule_switch_request(SN, 12, 1, seq)),
    ("delete_request", lambda seq: schedule_delete_request(SN, 6, seq)),
    (
        "edit_charge_request",
        lambda seq: schedule_save_request(
            SN, SCHEDULE_EDIT, schedule(start=hm(6), end=hm(8)), seq
        ),
    ),
    (
        "edit_use_battery_request",
        lambda seq: schedule_save_request(
            SN,
            SCHEDULE_EDIT,
            schedule(
                schedule_id=10,
                schedule_type=USE_BATTERY,
                enabled=True,
                power=5000,
                start=hm(8),
                end=hm(17),
            ),
            seq,
        ),
    ),
    (
        "add_charge_request",
        lambda seq: schedule_save_request(
            SN,
            SCHEDULE_ADD,
            schedule(
                schedule_id=11,
                enabled=True,
                power=3000,
                start=hm(8, 30),
                end=hm(9),
            ),
            seq,
        ),
    ),
]


def _use_battery(**changes: int | bool) -> Schedule:
    """Return the schedule 12 as the app had set it up: use battery, 3000 W."""
    values: dict[str, int | bool] = {
        "schedule_type": USE_BATTERY,
        "enabled": True,
        "power": 3000,
        "start": hm(9),
        "end": hm(11, 30),
    }
    return schedule(**(values | changes))


REQUESTS += [
    (
        "add_use_battery_request",
        lambda seq: schedule_save_request(
            SN, SCHEDULE_ADD, _use_battery(power=5000), seq
        ),
    ),
    (
        "edit_power_request",
        lambda seq: schedule_save_request(SN, SCHEDULE_EDIT, _use_battery(), seq),
    ),
    # The days: all seven ticked is "chosen days" (2) with 127, daily is 1 and 0
    (
        "days_all_request",
        lambda seq: schedule_save_request(
            SN, SCHEDULE_EDIT, _use_battery(repeat=2, days=127), seq
        ),
    ),
    (
        "daily_request",
        lambda seq: schedule_save_request(SN, SCHEDULE_EDIT, _use_battery(), seq),
    ),
    (
        "days_monday_request",
        lambda seq: schedule_save_request(
            SN, SCHEDULE_EDIT, _use_battery(repeat=2, days=1), seq
        ),
    ),
    (
        "days_sat_sun_request",
        lambda seq: schedule_save_request(
            SN, SCHEDULE_EDIT, _use_battery(repeat=2, days=32 + 64), seq
        ),
    ),
    (
        "days_mon_fri_request",
        lambda seq: schedule_save_request(
            SN, SCHEDULE_EDIT, _use_battery(repeat=2, days=31), seq
        ),
    ),
]


@pytest.mark.parametrize(("name", "build"), REQUESTS, ids=[r[0] for r in REQUESTS])
def test_request_as_the_app_sends_it(name: str, build: Callable[[int], bytes]) -> None:
    assert build(seq_of(name)) == frame(name)


def test_list_request_has_no_content_and_no_length() -> None:
    ((_, header),) = protobuf_fields(frame("list_request"))
    assert isinstance(header, bytes)
    numbers = [number for number, _ in protobuf_fields(header)]

    assert 1 not in numbers
    assert 10 not in numbers


# -----------------------------
# Replies to the commands
# -----------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("switch_off_reply", (12, None)),
        ("switch_on_reply", (12, None)),
        ("delete_reply", (6, None)),
        ("edit_charge_reply", (12, CHARGE)),
        ("edit_use_battery_reply", (10, USE_BATTERY)),
        ("add_charge_reply", (11, CHARGE)),
        ("add_use_battery_reply", (12, USE_BATTERY)),
    ],
)
def test_write_reply_names_the_schedule(
    name: str, expected: tuple[int, int | None]
) -> None:
    assert parse_schedule_write_reply(content_of(name)) == expected


def test_write_reply_without_content() -> None:
    assert parse_schedule_write_reply(b"") == (None, None)


# -----------------------------
# The list and a single schedule
# -----------------------------


def listed(
    schedule_id: int,
    schedule_type: int,
    power: int,
    start: int,
    end: int,
    **changes: int | bool,
) -> Schedule:
    """Return a schedule as the list reply of the capture shows it (all on)."""
    values: dict[str, int | bool] = {
        "schedule_id": schedule_id,
        "schedule_type": schedule_type,
        "enabled": True,
        "power": power,
        "start": start,
        "end": end,
        "repeat": 1,
        "days": 0,
        "in_use": False,
        "unknown_2": 2,
    }
    return Schedule(**(values | changes))  # type: ignore[arg-type]


def test_list_reply() -> None:
    assert parse_schedule_list(content_of("list_reply")) == [
        listed(10, USE_BATTERY, 5000, hm(8, 30), hm(9)),
        # Over midnight; the repeat 65 of older schedules is kept as it is
        listed(11, USE_BATTERY, 2000, hm(23), hm(2), repeat=65),
        listed(6, USE_BATTERY, 5000, hm(17), hm(19), repeat=65),
        listed(7, USE_BATTERY, 5000, hm(19), hm(23), repeat=65),
        listed(9, CHARGE, 3000, hm(2), hm(6)),
        listed(12, CHARGE, 1500, hm(6), hm(8, 30), in_use=True),
    ]


def test_list_reply_without_field_4_is_off() -> None:
    schedules = parse_schedule_list(content_of("list_reply_12_off"))

    off = next(s for s in schedules if s.schedule_id == 12)
    assert off.enabled is False
    assert all(s.enabled for s in schedules if s.schedule_id != 12)


def test_list_reply_after_the_system_changed_the_schedule_in_use() -> None:
    # 08:30:22: no message was sent, the system moved "in use" from 10 to 11
    schedules = parse_schedule_list(content_of("list_reply_11_in_use"))

    assert [(s.schedule_id, s.in_use) for s in schedules] == [
        (10, False),
        (11, True),
        (12, False),
    ]
    assert schedules[1].unknown_2 == 1


def test_list_reply_with_chosen_days() -> None:
    schedules = parse_schedule_list(content_of("list_reply_mon_fri"))

    assert (schedules[2].repeat, schedules[2].days) == (2, 31)


def test_single_schedule_reply() -> None:
    (part,) = split_message(frame("read_reply"))

    assert schedule_single(part) == schedule(unknown_2=2)


def test_incomplete_schedules_are_skipped() -> None:
    from custom_components.powerocean_single_phase.mqtt import (  # noqa: PLC0415
        _field_bytes,
        _field_varint,
    )

    no_id = _field_bytes(1, _field_varint(6, CHARGE))
    only_id = _field_bytes(1, _field_varint(3, 5))

    assert parse_schedule_list(no_id + only_id) == []


def test_schedule_list_and_single_only_from_their_command() -> None:
    reply = split_message(frame("list_reply"))[0]
    read = split_message(frame("read_reply"))[0]
    other = split_message(frame("switch_off_reply"))[0]

    assert [s.schedule_id for s in schedule_list(reply) or []] == [10, 11, 6, 7, 9, 12]
    assert schedule_list(read) is None
    assert schedule_list(other) is None
    assert schedule_single(reply) is None
    assert schedule_single(other) is None


def test_empty_list_reply_is_an_empty_list() -> None:
    # Unknown: what the system sends with no schedule left; no content is assumed
    # to mean none, not an error
    assert schedule_list(Part(96, 127, b"", 1)) == []


# -----------------------------
# The connection
# -----------------------------


def _callbacks() -> MqttCallbacks:
    return MqttCallbacks(
        on_modules=MagicMock(),
        on_settings=MagicMock(),
        on_ack=MagicMock(),
        on_energy_stream=MagicMock(),
        on_schedules=MagicMock(),
        on_schedule=MagicMock(),
    )


@pytest.fixture
def connection() -> tuple[PowerOceanMqtt, MqttCallbacks]:
    mqtt = PowerOceanMqtt(CREDENTIALS, SN, USER_ID, _callbacks())
    mqtt.client = MagicMock()
    return mqtt, mqtt.callbacks


def _receive(mqtt: PowerOceanMqtt, topic: str, payload: bytes) -> None:
    mqtt._on_message(mqtt.client, None, MagicMock(topic=topic, payload=payload))


def test_on_message_hands_over_the_list_before_the_ack(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection
    order = MagicMock()
    order.attach_mock(callbacks.on_schedules, "schedules")
    order.attach_mock(callbacks.on_ack, "ack")

    _receive(mqtt, SET_REPLY_TOPIC, frame("list_reply"))

    names = [call[0] for call in order.mock_calls]
    assert names == ["schedules", "ack"]
    callbacks.on_ack.assert_called_once_with(seq_of("list_reply"))
    handed_over = callbacks.on_schedules.call_args.args[0]
    assert [s.schedule_id for s in handed_over] == [10, 11, 6, 7, 9, 12]


def test_on_message_hands_over_a_single_schedule(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, SET_REPLY_TOPIC, frame("read_reply"))

    callbacks.on_schedule.assert_called_once()
    assert callbacks.on_schedule.call_args.args[0].schedule_id == 12
    callbacks.on_schedules.assert_not_called()


def test_on_message_acknowledges_a_schedule_command(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, SET_REPLY_TOPIC, frame("edit_charge_reply"))

    callbacks.on_ack.assert_called_once_with(seq_of("edit_charge_reply"))
    callbacks.on_schedules.assert_not_called()
    callbacks.on_schedule.assert_not_called()


def test_schedules_only_from_set_reply(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, callbacks = connection

    _receive(mqtt, DEVICE_TOPIC, frame("list_reply"))

    callbacks.on_schedules.assert_not_called()


def test_callbacks_for_schedules_are_optional() -> None:
    # The four older callbacks alone still work
    mqtt = PowerOceanMqtt(
        CREDENTIALS,
        SN,
        USER_ID,
        MqttCallbacks(
            on_modules=MagicMock(),
            on_settings=MagicMock(),
            on_ack=MagicMock(),
            on_energy_stream=MagicMock(),
        ),
    )
    mqtt.client = MagicMock()

    _receive(mqtt, SET_REPLY_TOPIC, frame("list_reply"))

    mqtt.callbacks.on_ack.assert_called_once()


@pytest.mark.parametrize(
    ("method", "args", "build"),
    [
        (
            "set_schedule_enabled",
            (12, 1),
            lambda seq: schedule_switch_request(SN, 12, 1, seq),
        ),
        ("delete_schedule", (6,), lambda seq: schedule_delete_request(SN, 6, seq)),
        (
            "save_schedule",
            (SCHEDULE_EDIT, schedule()),
            lambda seq: schedule_save_request(SN, SCHEDULE_EDIT, schedule(), seq),
        ),
    ],
)
def test_commands_publish_and_are_acknowledged_by_seq(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
    method: str,
    args: tuple,
    build: Callable[[int], bytes],
) -> None:
    mqtt, _ = connection

    getattr(mqtt, method)(*args, seq=1234)

    mqtt.client.publish.assert_called_once_with(
        f"/app/{USER_ID}/{SN}/thing/property/set", build(1234), qos=1
    )


def test_request_schedules_publishes_the_list_request(
    connection: tuple[PowerOceanMqtt, MqttCallbacks],
) -> None:
    mqtt, _ = connection

    mqtt.request_schedules()

    ((topic, payload), kwargs) = mqtt.client.publish.call_args
    assert topic == f"/app/{USER_ID}/{SN}/thing/property/set"
    assert kwargs == {"qos": 1}
    ((_, header),) = protobuf_fields(payload)
    fields = dict(protobuf_fields(header))
    assert (fields[8], fields[9]) == (96, 127)
