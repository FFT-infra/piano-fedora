// SPDX-License-Identifier: MIT
#include "calibration.hpp"
#include "processor.hpp"
#include "stream.hpp"
#include <algorithm>
#include <array>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <stdexcept>

namespace {
constexpr size_t kStockRecordSize = 5216;
constexpr size_t kStockPayloadOffset = 24;

uint64_t le64(const uint8_t *data) {
    uint64_t value = 0;
    for (unsigned int index = 0; index < 8; ++index)
        value |= uint64_t{data[index]} << (index * 8);
    return value;
}

struct Record {
    uint64_t timestamp_ns;
    uint64_t sequence;
    nvt::FingerFrame frame;
};

// Replays a private NTP1 capture, which may be a byte tail starting mid-record,
// through the same finger processor and prints each record's processing state.
// An incomplete final record (a byte head) is not processed.
int replayNtp1(const nvt::Calibration &calibration, const char *path) {
    std::ifstream input(path, std::ios::binary);
    if (!input)
        throw std::runtime_error("cannot open NTP1 capture");
    const std::vector<uint8_t> data((std::istreambuf_iterator<char>(input)), {});
    const std::array<uint8_t, 6> magic{'N', 'T', 'P', '1', 32, 0};
    const auto start = std::search(data.begin(), data.end(), magic.begin(), magic.end());
    nvt::FingerProcessor processor(calibration);
    nvt::StreamReader reader;
    uint64_t previous = 0, records = 0, processed = 0, baselines = 0, gaps = 0;
    reader.feed(std::span(start, data.end()), [&](const nvt::StreamRecord &record) {
        ++records;
        if ((record.flags & nvt::kStreamEpoch) || (previous && record.sequence != previous + 1)) {
            ++gaps;
            processor.reset();
        }
        previous = record.sequence;
        const auto packet = nvt::parsePacket(record.payload);
        nlohmann::json event{{"sequence", record.sequence}, {"timestamp_ns", record.timestamp_ns},
                             {"scan_mode", packet.scan_mode}, {"data_type", packet.data_type},
                             {"counter", packet.counter}};
        if (packet.data_type == 3) {
            const auto update = processor.feed(nvt::parseFingerFrame(packet), record.timestamp_ns);
            baselines += update.baseline_ready;
            event["baseline_ready"] = update.baseline_ready;
            if (update.result) {
                ++processed;
                const auto &result = *update.result;
                event["delta_max"] = *std::max_element(result.delta.begin(), result.delta.end());
                event["delta_min"] = *std::min_element(result.delta.begin(), result.delta.end());
                event["peaks"] = result.peaks.size();
                event["contacts"] = nlohmann::json::array();
                for (const auto &contact : result.contacts)
                    event["contacts"].push_back({{"x", contact.x}, {"y", contact.y},
                        {"peak", contact.peak}, {"area", contact.area}});
                event["tracked"] = nlohmann::json::array();
                for (const auto &slot : result.tracked_slots)
                    event["tracked"].push_back({{"slot", slot.number}, {"tracking_id", slot.tracking_id},
                        {"status", slot.status}, {"age", slot.age}, {"peak", slot.contact.peak}});
                event["slots"] = nlohmann::json::array();
                for (const auto &slot : result.slots)
                    event["slots"].push_back({{"slot", slot.number}, {"tracking_id", slot.tracking_id},
                        {"x", slot.contact.x}, {"y", slot.contact.y}});
            }
        }
        std::cout << event.dump() << '\n';
    });
    std::cerr << nlohmann::json{{"skipped_leading_bytes", start - data.begin()}, {"records", records},
        {"baseline_events", baselines}, {"processed_frames", processed}, {"discontinuities", gaps},
        {"scope", "Offline NTP1 replay in processor axes; not hardware input validation"}}.dump() << '\n';
    return 0;
}
}

int main(int argc, char **argv) {
    const std::string_view mode = argc == 4 ? argv[1] : "";
    if (mode != "--stock-ring" && mode != "--ntp1") {
        std::cerr << "Usage: piano-thp-replay --stock-ring CALIBRATION.json RECORDS.bin\n"
                  << "       piano-thp-replay --ntp1 CALIBRATION.json CAPTURE.ntp1\n"
                  << "Offline replay only; no input device, firmware command or tablet access.\n";
        return 2;
    }
    try {
        const auto calibration = nvt::loadCalibration(argv[2]);
        if (mode == "--ntp1")
            return replayNtp1(calibration, argv[3]);
        nvt::FingerProcessor processor(calibration);
        std::ifstream input(argv[3], std::ios::binary);
        if (!input)
            throw std::runtime_error("cannot open stock records");
        std::vector<Record> records;
        std::array<uint8_t, kStockRecordSize> raw{};
        while (input.read(reinterpret_cast<char *>(raw.data()), raw.size())) {
            const auto timestamp = le64(raw.data());
            if (timestamp == 0 || timestamp > uint64_t{INT64_MAX})
                throw std::runtime_error("invalid stock realtime timestamp");
            records.push_back({timestamp, le64(raw.data() + 8),
                nvt::parseFingerFrame(std::span(raw).subspan(kStockPayloadOffset))});
        }
        if (!input.eof() || input.gcount() != 0 || records.empty())
            throw std::runtime_error("empty or truncated stock record input");
        std::sort(records.begin(), records.end(), [](const auto &a, const auto &b) {
            return a.sequence < b.sequence;
        });
        size_t processed = 0, baselines = 0, contacts = 0;
        for (size_t index = 0; index < records.size(); ++index) {
            const auto &record = records[index];
            if (index && (record.sequence == records[index - 1].sequence ||
                          record.timestamp_ns <= records[index - 1].timestamp_ns))
                throw std::runtime_error("duplicate sequence or non-monotonic stock time");
            const auto update = processor.feed(record.frame, record.timestamp_ns);
            nlohmann::json event{{"sequence", record.sequence}, {"timestamp_ns", record.timestamp_ns},
                                {"scan_mode", record.frame.scan_mode}, {"baseline_ready", update.baseline_ready},
                                {"slots", nlohmann::json::array()}};
            if (update.baseline_ready)
                ++baselines;
            if (update.result) {
                ++processed;
                for (const auto &slot : update.result->slots) {
                    event["slots"].push_back({{"slot", slot.number}, {"tracking_id", slot.tracking_id},
                        {"x", slot.contact.x}, {"y", slot.contact.y}});
                    ++contacts;
                }
            }
            std::cout << event.dump() << '\n';
        }
        std::cerr << nlohmann::json{{"records", records.size()}, {"baseline_events", baselines},
            {"processed_frames", processed}, {"reported_slots", contacts},
            {"scope", "Offline stock-ring replay; not hardware input or coordinate calibration validation"}}.dump() << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
