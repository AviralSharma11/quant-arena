#pragma once

#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace quant_arena::engine {

enum class Side : std::uint8_t {
    Buy = 1,
    Sell = 2,
};

struct Order {
    long long order_id;
    long long user_id;
    std::string symbol;
    Side side;
    long long price;
    long long quantity;
    long long created_at;
    long long remaining_quantity;

    Order(long long order_id_,
          long long user_id_,
          const std::string& symbol_,
          Side side_,
          long long price_,
          long long quantity_,
          long long created_at_)
        : order_id(order_id_),
          user_id(user_id_),
          symbol(symbol_),
          side(side_),
          price(price_),
          quantity(quantity_),
          created_at(created_at_),
          remaining_quantity(quantity_) {
        if (price <= 0) {
            throw std::runtime_error("price must be positive");
        }
        if (quantity <= 0) {
            throw std::runtime_error("quantity must be positive");
        }
    }

    bool is_open() const {
        return remaining_quantity > 0;
    }

    void reduce(long long qty) {
        if (qty <= 0) {
            throw std::runtime_error("qty must be positive");
        }
        if (qty > remaining_quantity) {
            throw std::runtime_error("cannot reduce more than the remaining quantity");
        }
        remaining_quantity -= qty;
    }
};

struct Fill {
    long long buy_order_id;
    long long sell_order_id;
    std::string symbol;
    long long price;
    long long quantity;
};

class PriceLevel {
public:
    explicit PriceLevel(long long price);

    long long price() const;
    void append_order_id(long long order_id);
    bool remove_order_id(long long order_id);
    std::optional<long long> front_order_id() const;
    bool empty() const;

private:
    long long price_;
    std::deque<long long> order_ids_;
};

class OrderBook {
public:
    OrderBook() = default;

    void add_order(const Order& order);
    std::optional<Order> remove_order(long long order_id);
    void cancel_order(long long order_id);

    std::vector<Fill> match();
    std::vector<Fill> add_order_and_match(const Order& order);

    std::optional<long long> best_bid() const;
    std::optional<long long> best_ask() const;
    std::optional<Order> best_bid_order();
    std::optional<Order> best_ask_order();
    std::optional<Order> best_bid_order() const;
    std::optional<Order> best_ask_order() const;
    std::optional<Order> get_order(long long order_id);
    std::optional<Order> get_order(long long order_id) const;
    bool has_orders() const;

private:
    std::map<long long, PriceLevel, std::greater<long long>> bids_;
    std::map<long long, PriceLevel> asks_;
    std::map<long long, Order> orders_by_id_;
};

}  // namespace quant_arena::engine
