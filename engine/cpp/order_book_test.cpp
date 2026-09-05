#include "order_book.hpp"

#include <iostream>
#include <stdexcept>
#include <string>

using quant_arena::engine::Fill;
using quant_arena::engine::Order;
using quant_arena::engine::OrderBook;
using quant_arena::engine::Side;

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

}  // namespace

int main() {
    try {
        {
            OrderBook book;
            book.add_order(Order{1, 10, "QA-TECH", Side::Buy, 100, 10, 1});
            book.add_order(Order{2, 11, "QA-TECH", Side::Sell, 99, 6, 2});
            auto fills = book.match();
            check(fills.size() == 1, "single match creates one fill");
            check(fills[0].buy_order_id == 1, "buy order id is correct");
            check(fills[0].sell_order_id == 2, "sell order id is correct");
            check(fills[0].quantity == 6, "fill quantity is correct");
            check(book.best_bid().value_or(-1) == 100, "remaining buy is still on the book");
            check(!book.best_ask().has_value(), "sell side is emptied");
        }

        {
            OrderBook book;
            book.add_order(Order{3, 20, "QA-TECH", Side::Buy, 100, 20, 1});
            book.add_order(Order{4, 21, "QA-TECH", Side::Sell, 99, 8, 2});
            auto fills = book.match();
            check(fills.size() == 1, "partial fill creates one fill");
            check(fills[0].price == 100, "seller aggressor trades at the resting bid price");
            check(fills[0].quantity == 8, "partial fill quantity is correct");
            check(book.get_order(3)->remaining_quantity == 12, "remaining buy quantity is correct");
            check(!book.best_ask().has_value(), "sell order was fully consumed");
        }

        {
            OrderBook book;
            book.add_order(Order{5, 30, "QA-TECH", Side::Buy, 98, 10, 1});
            book.add_order(Order{6, 31, "QA-TECH", Side::Sell, 100, 5, 2});
            auto fills = book.match();
            check(fills.empty(), "no crossing order produces no fills");
            check(book.best_bid().value_or(-1) == 98, "buy remains at best bid");
            check(book.best_ask().value_or(-1) == 100, "sell remains at best ask");
        }

        {
            OrderBook book;
            book.add_order(Order{7, 40, "QA-TECH", Side::Buy, 100, 5, 1});
            book.add_order(Order{8, 41, "QA-TECH", Side::Buy, 100, 7, 2});
            book.add_order(Order{9, 42, "QA-TECH", Side::Sell, 100, 8, 3});
            auto fills = book.match();
            check(fills.size() == 2, "same-price matching continues until no crossing remains");
            check(fills[0].buy_order_id == 7, "earlier buy gets priority for first fill");
            check(fills[1].buy_order_id == 8, "later buy executes next after the first trade");
            check(book.get_order(8).has_value(), "later buy remains on the book after the second fill");
            check(book.get_order(8).value().remaining_quantity == 4, "later buy retains the unfilled remainder");
            check(book.get_order(9).has_value() == false, "sell order is fully consumed");
        }

        {
            OrderBook book;
            book.add_order(Order{10, 50, "QA-TECH", Side::Buy, 100, 4, 1});
            book.add_order(Order{11, 51, "QA-TECH", Side::Sell, 99, 2, 2});
            book.cancel_order(10);
            check(!book.get_order(10).has_value(), "cancelled buy is removed");
            check(book.best_bid().has_value() == false, "book is empty after cancel");
        }

        {
            OrderBook book;
            book.add_order(Order{12, 60, "QA-TECH", Side::Buy, 100, 4, 1});
            book.add_order(Order{13, 61, "QA-TECH", Side::Buy, 100, 4, 2});
            book.add_order(Order{14, 62, "QA-TECH", Side::Sell, 100, 7, 3});
            auto fills = book.match();
            check(fills.size() == 2, "same-price book drains crossing quantity in FIFO order");
            check(fills[0].buy_order_id == 12, "earliest buy is matched first");
            check(fills[1].buy_order_id == 13, "next buy is matched second");
            check(book.best_bid().value_or(-1) == 100, "remaining buy stays at the same best price");
            check(book.best_ask().has_value() == false, "sell side is fully consumed");
            check(book.get_order(13).value().remaining_quantity == 1, "second buy keeps its unfilled remainder at the same price");
        }

        {
            OrderBook book;
            book.add_order(Order{15, 70, "QA-TECH", Side::Buy, 100, 10, 1});
            book.add_order(Order{16, 71, "QA-TECH", Side::Sell, 99, 8, 2});
            auto fills = book.match();
            check(fills.size() == 1, "partial fill creates only one trade");
            check(book.get_order(15).value().remaining_quantity == 2, "resting buy keeps remaining quantity");
            book.cancel_order(15);
            check(!book.get_order(15).has_value(), "cancelling a partially filled order removes it safely");
            check(book.best_bid().has_value() == false, "book is empty after cancellation of the remaining rest");
        }

        std::cout << "All C++ order book tests passed." << std::endl;
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "C++ engine test failure: " << ex.what() << std::endl;
        return 1;
    }
}
