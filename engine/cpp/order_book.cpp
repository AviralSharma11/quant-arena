#include "order_book.hpp"

#include <algorithm>
#include <stdexcept>

namespace quant_arena::engine {

PriceLevel::PriceLevel(long long price) : price_(price) {
    if (price <= 0) {
        throw std::runtime_error("price must be positive");
    }
}

long long PriceLevel::price() const {
    return price_;
}

void PriceLevel::append_order_id(long long order_id) {
    order_ids_.push_back(order_id);
}

bool PriceLevel::remove_order_id(long long order_id) {
    auto it = std::find(order_ids_.begin(), order_ids_.end(), order_id);
    if (it == order_ids_.end()) {
        return false;
    }
    order_ids_.erase(it);
    return true;
}

std::optional<long long> PriceLevel::front_order_id() const {
    if (order_ids_.empty()) {
        return std::nullopt;
    }
    return order_ids_.front();
}

bool PriceLevel::empty() const {
    return order_ids_.empty();
}

void OrderBook::add_order(const Order& order) {
    if (orders_by_id_.count(order.order_id) != 0) {
        throw std::runtime_error("order already exists in the book");
    }

    orders_by_id_.emplace(order.order_id, order);
    arrival_sequence_.emplace(order.order_id, next_arrival_sequence_++);

    if (order.side == Side::Buy) {
        auto level_it = bids_.find(order.price);
        if (level_it == bids_.end()) {
            level_it = bids_.emplace(order.price, PriceLevel(order.price)).first;
        }
        level_it->second.append_order_id(order.order_id);
        return;
    }

    auto level_it = asks_.find(order.price);
    if (level_it == asks_.end()) {
        level_it = asks_.emplace(order.price, PriceLevel(order.price)).first;
    }
    level_it->second.append_order_id(order.order_id);
}

std::optional<Order> OrderBook::remove_order(long long order_id) {
    auto it = orders_by_id_.find(order_id);
    if (it == orders_by_id_.end()) {
        return std::nullopt;
    }

    Order removed = it->second;
    if (removed.side == Side::Buy) {
        auto level_it = bids_.find(removed.price);
        if (level_it != bids_.end()) {
            level_it->second.remove_order_id(order_id);
            if (level_it->second.empty()) {
                bids_.erase(level_it);
            }
        }
    } else {
        auto level_it = asks_.find(removed.price);
        if (level_it != asks_.end()) {
            level_it->second.remove_order_id(order_id);
            if (level_it->second.empty()) {
                asks_.erase(level_it);
            }
        }
    }

    orders_by_id_.erase(it);
    arrival_sequence_.erase(order_id);
    return removed;
}

void OrderBook::cancel_order(long long order_id) {
    remove_order(order_id);
}

std::optional<Order> OrderBook::get_order(long long order_id) {
    auto it = orders_by_id_.find(order_id);
    if (it == orders_by_id_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::optional<Order> OrderBook::get_order(long long order_id) const {
    auto it = orders_by_id_.find(order_id);
    if (it == orders_by_id_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::optional<long long> OrderBook::best_bid() const {
    if (bids_.empty()) {
        return std::nullopt;
    }
    return bids_.begin()->first;
}

std::optional<long long> OrderBook::best_ask() const {
    if (asks_.empty()) {
        return std::nullopt;
    }
    return asks_.begin()->first;
}

std::optional<Order> OrderBook::best_bid_order() {
    auto price = best_bid();
    if (!price.has_value()) {
        return std::nullopt;
    }
    auto level_it = bids_.find(*price);
    if (level_it == bids_.end()) {
        return std::nullopt;
    }
    auto order_id = level_it->second.front_order_id();
    if (!order_id.has_value()) {
        return std::nullopt;
    }
    return get_order(*order_id).value();
}

std::optional<Order> OrderBook::best_ask_order() {
    auto price = best_ask();
    if (!price.has_value()) {
        return std::nullopt;
    }
    auto level_it = asks_.find(*price);
    if (level_it == asks_.end()) {
        return std::nullopt;
    }
    auto order_id = level_it->second.front_order_id();
    if (!order_id.has_value()) {
        return std::nullopt;
    }
    return get_order(*order_id).value();
}

std::optional<Order> OrderBook::best_bid_order() const {
    auto price = best_bid();
    if (!price.has_value()) {
        return std::nullopt;
    }
    auto level_it = bids_.find(*price);
    if (level_it == bids_.end()) {
        return std::nullopt;
    }
    auto order_id = level_it->second.front_order_id();
    if (!order_id.has_value()) {
        return std::nullopt;
    }
    return get_order(*order_id).value();
}

std::optional<Order> OrderBook::best_ask_order() const {
    auto price = best_ask();
    if (!price.has_value()) {
        return std::nullopt;
    }
    auto level_it = asks_.find(*price);
    if (level_it == asks_.end()) {
        return std::nullopt;
    }
    auto order_id = level_it->second.front_order_id();
    if (!order_id.has_value()) {
        return std::nullopt;
    }
    return get_order(*order_id).value();
}

bool OrderBook::has_orders() const {
    return !orders_by_id_.empty();
}

std::vector<Fill> OrderBook::match() {
    std::vector<Fill> fills;

    while (true) {
        auto bid_price = best_bid();
        auto ask_price = best_ask();
        if (!bid_price.has_value() || !ask_price.has_value()) {
            return fills;
        }

        if (*bid_price < *ask_price) {
            return fills;
        }

        auto bid_level_it = bids_.find(*bid_price);
        auto ask_level_it = asks_.find(*ask_price);
        if (bid_level_it == bids_.end() || ask_level_it == asks_.end()) {
            return fills;
        }

        auto bid_order_id = bid_level_it->second.front_order_id();
        auto ask_order_id = ask_level_it->second.front_order_id();
        if (!bid_order_id.has_value() || !ask_order_id.has_value()) {
            return fills;
        }

        auto bid_it = orders_by_id_.find(*bid_order_id);
        auto ask_it = orders_by_id_.find(*ask_order_id);
        if (bid_it == orders_by_id_.end() || ask_it == orders_by_id_.end()) {
            return fills;
        }

        Order& bid_order = bid_it->second;
        Order& ask_order = ask_it->second;

        long long trade_qty = std::min(bid_order.remaining_quantity, ask_order.remaining_quantity);
        const auto bid_arrival = arrival_sequence_.at(bid_order.order_id);
        const auto ask_arrival = arrival_sequence_.at(ask_order.order_id);
        const long long maker_price =
            bid_arrival < ask_arrival ? bid_order.price : ask_order.price;
        Fill fill{bid_order.order_id, ask_order.order_id, ask_order.symbol, maker_price, trade_qty};
        fills.push_back(fill);

        bid_order.reduce(trade_qty);
        ask_order.reduce(trade_qty);

        if (bid_order.remaining_quantity == 0) {
            remove_order(bid_order.order_id);
        }
        if (ask_order.remaining_quantity == 0) {
            remove_order(ask_order.order_id);
        }
    }
}

std::vector<Fill> OrderBook::add_order_and_match(const Order& order) {
    add_order(order);
    return match();
}

}  // namespace quant_arena::engine
