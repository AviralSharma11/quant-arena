#include "order_book.hpp"
#include "contracts/v1/generated/contracts.hpp"

#include <bit>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

using Byte = std::uint8_t;
using Bytes = std::vector<Byte>;
using quant_arena::engine::Fill;
using quant_arena::engine::Order;
using quant_arena::engine::OrderBook;
using quant_arena::engine::Side;
namespace contracts = quant_arena::contracts::v1;

static_assert(std::endian::native == std::endian::little,
              "The wire contract requires a little-endian execution target");

template <typename Record>
Record unpack_record(const Bytes& data) {
    if (data.size() != sizeof(Record)) {
        throw std::runtime_error("record size does not match the generated contract");
    }
    Record record{};
    std::memcpy(&record, data.data(), sizeof(record));
    return record;
}

template <typename Record>
Bytes pack_record(const Record& record) {
    Bytes data(sizeof(record));
    std::memcpy(data.data(), &record, sizeof(record));
    return data;
}

contracts::RecordHeader unpack_header(const Bytes& data) {
    if (data.size() < sizeof(contracts::RecordHeader)) {
        throw std::runtime_error("record is shorter than the generated contract header");
    }
    contracts::RecordHeader header{};
    std::memcpy(&header, data.data(), sizeof(header));
    if (header.schema_version != contracts::SCHEMA_VERSION) {
        throw std::runtime_error("unsupported schema version");
    }
    return header;
}

template <typename Record>
Record new_record(contracts::RecordType record_type) {
    Record record{};
    record.schema_version = contracts::SCHEMA_VERSION;
    record.record_type = record_type;
    record.seq_ms = contracts::SEQ_UNASSIGNED;
    record.seq_ord = contracts::SEQ_UNASSIGNED;
    return record;
}

std::uint32_t read_frame_length() {
    Byte bytes[4];
    std::cin.read(reinterpret_cast<char*>(bytes), sizeof(bytes));
    if (std::cin.eof() && std::cin.gcount() == 0) {
        return 0;
    }
    if (std::cin.gcount() != 4) {
        throw std::runtime_error("truncated input frame length");
    }
    return static_cast<std::uint32_t>(bytes[0]) |
           (static_cast<std::uint32_t>(bytes[1]) << 8) |
           (static_cast<std::uint32_t>(bytes[2]) << 16) |
           (static_cast<std::uint32_t>(bytes[3]) << 24);
}

Bytes read_frame(std::uint32_t length) {
    Bytes data(length);
    if (length == 0) {
        return data;
    }
    std::cin.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(length));
    if (std::cin.gcount() != static_cast<std::streamsize>(length)) {
        throw std::runtime_error("truncated input frame");
    }
    return data;
}

void write_frame(const Bytes& data) {
    const std::uint32_t length = static_cast<std::uint32_t>(data.size());
    Byte header[4]{
        static_cast<Byte>(length & 0xff),
        static_cast<Byte>((length >> 8) & 0xff),
        static_cast<Byte>((length >> 16) & 0xff),
        static_cast<Byte>((length >> 24) & 0xff),
    };
    std::cout.write(reinterpret_cast<const char*>(header), sizeof(header));
    if (!data.empty()) {
        std::cout.write(reinterpret_cast<const char*>(data.data()),
                        static_cast<std::streamsize>(data.size()));
    }
    std::cout.flush();
}

struct LiveOrder {
    std::uint64_t order_id;
    std::uint64_t client_order_id;
    std::uint64_t user_id;
    std::int16_t symbol_id;
    std::uint8_t side;
    std::int64_t price_ticks;
};

class StreamEngine {
public:
    explicit StreamEngine(std::int64_t initial_cash_ticks)
        : initial_cash_ticks_(initial_cash_ticks) {}

    std::vector<Bytes> apply(const Bytes& record) {
        const auto header = unpack_header(record);
        switch (header.record_type) {
        case contracts::RecordType::SUBMIT_ORDER:
            return submit(unpack_record<contracts::SubmitOrder>(record));
        case contracts::RecordType::CANCEL_ORDER:
            return cancel(unpack_record<contracts::CancelOrder>(record));
        case contracts::RecordType::CREATE_ACCOUNT:
            return create_account(unpack_record<contracts::CreateAccount>(record));
        case contracts::RecordType::CREDIT_CASH:
            return credit_cash(unpack_record<contracts::CreditCash>(record));
        case contracts::RecordType::CONFIGURE_REPLAY:
            return configure_replay(unpack_record<contracts::ConfigureReplay>(record));
        default:
            return {};
        }
    }

private:
   using ClientKey = std::pair<std::uint64_t, std::uint64_t>;

    OrderBook& book(std::int16_t symbol_id) {
        return books_[symbol_id];
    }

    static ClientKey client_key(std::uint64_t user_id, std::uint64_t client_order_id) {
        return {user_id, client_order_id};
    }

    Bytes rejected(const contracts::SubmitOrder& input, contracts::RejectReason reason) const {
        auto output = new_record<contracts::OrderRejected>(contracts::RecordType::ORDER_REJECTED);
        output.timestamp_ns = input.timestamp_ns;
        output.client_order_id = input.client_order_id;
        output.user_id = input.user_id;
        output.symbol_id = input.symbol_id;
        output.reason = reason;
        return pack_record(output);
    }

    Bytes rejected_cancel(const contracts::CancelOrder& input,
                          contracts::RejectReason reason) const {
        auto output = new_record<contracts::OrderRejected>(contracts::RecordType::ORDER_REJECTED);
        output.timestamp_ns = input.timestamp_ns;
        output.client_order_id = input.client_order_id;
        output.user_id = input.user_id;
        output.symbol_id = 0;
        output.reason = reason;
        return pack_record(output);
    }

    Bytes accepted(const contracts::SubmitOrder& input, std::uint64_t order_id) const {
        auto output = new_record<contracts::OrderAccepted>(contracts::RecordType::ORDER_ACCEPTED);
        output.timestamp_ns = input.timestamp_ns;
        output.order_id = order_id;
        output.client_order_id = input.client_order_id;
        output.user_id = input.user_id;
        output.price_ticks = input.price_ticks;
        output.qty = input.qty;
        output.symbol_id = input.symbol_id;
        output.side = input.side;
        output.tif = input.tif;
        return pack_record(output);
    }

    Bytes fill_record(const contracts::SubmitOrder& input, const Fill& fill, const LiveOrder& maker,
                      const LiveOrder& taker) const {
        auto output = new_record<contracts::Fill>(contracts::RecordType::FILL);
        output.timestamp_ns = input.timestamp_ns;
        output.maker_order_id = maker.order_id;
        output.taker_order_id = taker.order_id;
        output.maker_user_id = maker.user_id;
        output.taker_user_id = taker.user_id;
        output.price_ticks = fill.price;
        output.qty = fill.quantity;
        output.symbol_id = maker.symbol_id;
        output.aggressor_side = static_cast<contracts::Side>(taker.side);
        return pack_record(output);
    }

    Bytes cancelled(std::int64_t timestamp_ns, const LiveOrder& order,
                    std::int64_t remaining, contracts::CancelReason reason) const {
        auto output =
            new_record<contracts::OrderCancelled>(contracts::RecordType::ORDER_CANCELLED);
        output.timestamp_ns = timestamp_ns;
        output.order_id = order.order_id;
        output.client_order_id = order.client_order_id;
        output.user_id = order.user_id;
        output.remaining_qty = remaining;
        output.symbol_id = order.symbol_id;
        output.reason = reason;
        return pack_record(output);
    }

    void forget_if_filled(std::int16_t symbol_id, std::uint64_t order_id) {
        if (book(symbol_id).get_order(static_cast<long long>(order_id)).has_value()) {
            return;
        }
        const auto live = live_.find(order_id);
        if (live == live_.end()) {
            return;
        }
        client_to_order_.erase(client_key(live->second.user_id, live->second.client_order_id));
        live_.erase(live);
    }

    std::vector<Bytes> submit(const contracts::SubmitOrder& input) {
        if (input.price_ticks <= 0) {
            return {rejected(input, contracts::RejectReason::INVALID_PRICE)};
        }
        if (input.qty <= 0) {
            return {rejected(input, contracts::RejectReason::INVALID_QUANTITY)};
        }
        if (input.side != contracts::Side::BUY && input.side != contracts::Side::SELL) {
            return {rejected(input, contracts::RejectReason::INVALID_SIDE)};
        }
        if (input.tif != contracts::Tif::GTC && input.tif != contracts::Tif::IOC) {
            return {rejected(input, contracts::RejectReason::INVALID_TIF)};
        }

        const auto order_id = next_order_id_++;
        const auto user_id = input.user_id;
        const auto client_order_id = input.client_order_id;
        const auto symbol_id = input.symbol_id;
        const LiveOrder live{
            order_id, client_order_id, user_id, symbol_id,
            static_cast<std::uint8_t>(input.side), input.price_ticks,
        };
        const auto key = client_key(user_id, client_order_id);
        client_to_order_[key] = order_id;
        live_[order_id] = live;

        auto& target_book = book(symbol_id);
        target_book.add_order(Order{
            static_cast<long long>(order_id),
            static_cast<long long>(user_id),
            std::to_string(symbol_id),
            input.side == contracts::Side::BUY ? Side::Buy : Side::Sell,
            static_cast<long long>(input.price_ticks),
            static_cast<long long>(input.qty),
            input.timestamp_ns,
        });

        std::vector<Bytes> output{accepted(input, order_id)};
        const auto fills = target_book.match();
        for (const auto& fill : fills) {
            const auto buy = live_.at(static_cast<std::uint64_t>(fill.buy_order_id));
            const auto sell = live_.at(static_cast<std::uint64_t>(fill.sell_order_id));
            const auto& maker = buy.order_id == order_id ? sell : buy;
            const auto& taker = buy.order_id == order_id ? buy : sell;
            output.push_back(fill_record(input, fill, maker, taker));
        }

        for (const auto& fill : fills) {
            forget_if_filled(symbol_id, static_cast<std::uint64_t>(fill.buy_order_id));
            forget_if_filled(symbol_id, static_cast<std::uint64_t>(fill.sell_order_id));
        }

        const auto current = live_.find(order_id);
        if (input.tif == contracts::Tif::IOC && current != live_.end()) {
            const auto order = target_book.get_order(static_cast<long long>(order_id));
            if (order.has_value() && order->remaining_quantity > 0) {
                const auto remaining = order->remaining_quantity;
                target_book.cancel_order(static_cast<long long>(order_id));
                output.push_back(
                    cancelled(input.timestamp_ns, current->second, remaining,
                              contracts::CancelReason::IOC_EXPIRED));
                client_to_order_.erase(key);
                live_.erase(current);
            }
        }
        return output;
    }

    std::vector<Bytes> cancel(const contracts::CancelOrder& input) {
        const auto user_id = input.user_id;
        const auto target_client_order_id = input.target_client_order_id;
        const auto lookup = client_to_order_.find(client_key(user_id, target_client_order_id));
        if (lookup == client_to_order_.end()) {
            return {rejected_cancel(input, contracts::RejectReason::UNKNOWN_ORDER)};
        }

        const auto live_it = live_.find(lookup->second);
        if (live_it == live_.end()) {
            throw std::runtime_error("client order index points to a missing live order");
        }
        const auto order = live_it->second;
        auto& target_book = book(order.symbol_id);
        const auto current = target_book.get_order(static_cast<long long>(order.order_id));
        if (!current.has_value()) {
            client_to_order_.erase(lookup);
            live_.erase(live_it);
            return {rejected_cancel(input, contracts::RejectReason::UNKNOWN_ORDER)};
        }
        const auto remaining = current->remaining_quantity;
        target_book.cancel_order(static_cast<long long>(order.order_id));
        client_to_order_.erase(lookup);
        live_.erase(live_it);
        return {cancelled(input.timestamp_ns, order, remaining,
                          contracts::CancelReason::USER_REQUESTED)};
    }

    std::vector<Bytes> create_account(const contracts::CreateAccount& input) const {
        auto output = new_record<contracts::AccountCreated>(contracts::RecordType::ACCOUNT_CREATED);
        output.timestamp_ns = input.timestamp_ns;
        output.client_order_id = input.client_order_id;
        output.user_id = input.user_id;
        output.initial_cash_ticks = initial_cash_ticks_;
        return {pack_record(output)};
    }

    std::vector<Bytes> credit_cash(const contracts::CreditCash& input) const {
        auto output = new_record<contracts::CashCredited>(contracts::RecordType::CASH_CREDITED);
        output.timestamp_ns = input.timestamp_ns;
        output.client_order_id = input.client_order_id;
        output.user_id = input.user_id;
        output.amount_ticks = input.amount_ticks;
        return {pack_record(output)};
    }

    std::vector<Bytes> configure_replay(const contracts::ConfigureReplay& input) const {
        auto output =
            new_record<contracts::ReplayConfigured>(contracts::RecordType::REPLAY_CONFIGURED);
        output.timestamp_ns = input.timestamp_ns;
        output.client_order_id = input.client_order_id;
        output.real_seconds_per_simulated_minute = input.real_seconds_per_simulated_minute;
        output.config_hash_hi = input.config_hash_hi;
        output.config_hash_lo = input.config_hash_lo;
        return {pack_record(output)};
    }

    std::int64_t initial_cash_ticks_;
    std::uint64_t next_order_id_{1};
    std::map<std::int16_t, OrderBook> books_;
    std::map<std::uint64_t, LiveOrder> live_;
    std::map<ClientKey, std::uint64_t> client_to_order_;
};

std::int64_t initial_cash_from_args(int argc, char** argv) {
    if (argc != 3 || std::string(argv[1]) != "--initial-cash-ticks") {
        throw std::runtime_error(
            "usage: quant-arena-engine --initial-cash-ticks <signed integer>");
    }
    std::size_t consumed = 0;
    const auto value = std::stoll(argv[2], &consumed);
    if (consumed != std::strlen(argv[2])) {
        throw std::runtime_error("initial cash must be an integer");
    }
    return value;
}

}  // namespace

int main(int argc, char** argv) {
    try {
#ifdef _WIN32
        _setmode(_fileno(stdin), _O_BINARY);
        _setmode(_fileno(stdout), _O_BINARY);
#endif
        StreamEngine engine(initial_cash_from_args(argc, argv));
        while (true) {
            const auto length = read_frame_length();
            if (!std::cin && length == 0) {
                break;
            }
            if (length == 0) {
                throw std::runtime_error("zero-length input records are not allowed");
            }
            const auto input = read_frame(length);
            for (const auto& output : engine.apply(input)) {
                write_frame(output);
            }
            write_frame({});
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "quant-arena-engine: " << error.what() << '\n';
        return 1;
    }
}
