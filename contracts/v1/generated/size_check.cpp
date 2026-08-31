// GENERATED FILE — DO NOT EDIT.
//
// Source:     contracts/v1/schema.toml
// Source sha: 18bb7c3798cd8f892f94bb249bb840544e41f144139c46ded9b20c4cc29ea375
// Regenerate: python contracts/v1/generate.py
//
// Hand-editing this file reintroduces exactly the C++/Python drift the generator exists to
// prevent. contracts/v1/tests/test_generated_is_current.py fails if you do.

// Prints the real sizeof and offsetof of every record, for
// contracts/v1/tests/test_sizes.py to compare against the Python module.

#include <cstddef>
#include <cstdio>

#include "contracts.hpp"

using namespace quant_arena::contracts::v1;

int main() {
  std::printf("size SubmitOrder %zu\n", sizeof(SubmitOrder));
  std::printf("offset SubmitOrder schema_version %zu\n", offsetof(SubmitOrder, schema_version));
  std::printf("offset SubmitOrder record_type %zu\n", offsetof(SubmitOrder, record_type));
  std::printf("offset SubmitOrder seq_ms %zu\n", offsetof(SubmitOrder, seq_ms));
  std::printf("offset SubmitOrder seq_ord %zu\n", offsetof(SubmitOrder, seq_ord));
  std::printf("offset SubmitOrder timestamp_ns %zu\n", offsetof(SubmitOrder, timestamp_ns));
  std::printf("offset SubmitOrder client_order_id %zu\n", offsetof(SubmitOrder, client_order_id));
  std::printf("offset SubmitOrder user_id %zu\n", offsetof(SubmitOrder, user_id));
  std::printf("offset SubmitOrder price_ticks %zu\n", offsetof(SubmitOrder, price_ticks));
  std::printf("offset SubmitOrder qty %zu\n", offsetof(SubmitOrder, qty));
  std::printf("offset SubmitOrder symbol_id %zu\n", offsetof(SubmitOrder, symbol_id));
  std::printf("offset SubmitOrder side %zu\n", offsetof(SubmitOrder, side));
  std::printf("offset SubmitOrder tif %zu\n", offsetof(SubmitOrder, tif));
  std::printf("size CancelOrder %zu\n", sizeof(CancelOrder));
  std::printf("offset CancelOrder schema_version %zu\n", offsetof(CancelOrder, schema_version));
  std::printf("offset CancelOrder record_type %zu\n", offsetof(CancelOrder, record_type));
  std::printf("offset CancelOrder seq_ms %zu\n", offsetof(CancelOrder, seq_ms));
  std::printf("offset CancelOrder seq_ord %zu\n", offsetof(CancelOrder, seq_ord));
  std::printf("offset CancelOrder timestamp_ns %zu\n", offsetof(CancelOrder, timestamp_ns));
  std::printf("offset CancelOrder client_order_id %zu\n", offsetof(CancelOrder, client_order_id));
  std::printf("offset CancelOrder user_id %zu\n", offsetof(CancelOrder, user_id));
  std::printf("offset CancelOrder target_client_order_id %zu\n", offsetof(CancelOrder, target_client_order_id));
  std::printf("size CreateAccount %zu\n", sizeof(CreateAccount));
  std::printf("offset CreateAccount schema_version %zu\n", offsetof(CreateAccount, schema_version));
  std::printf("offset CreateAccount record_type %zu\n", offsetof(CreateAccount, record_type));
  std::printf("offset CreateAccount seq_ms %zu\n", offsetof(CreateAccount, seq_ms));
  std::printf("offset CreateAccount seq_ord %zu\n", offsetof(CreateAccount, seq_ord));
  std::printf("offset CreateAccount timestamp_ns %zu\n", offsetof(CreateAccount, timestamp_ns));
  std::printf("offset CreateAccount client_order_id %zu\n", offsetof(CreateAccount, client_order_id));
  std::printf("offset CreateAccount user_id %zu\n", offsetof(CreateAccount, user_id));
  std::printf("size CreditCash %zu\n", sizeof(CreditCash));
  std::printf("offset CreditCash schema_version %zu\n", offsetof(CreditCash, schema_version));
  std::printf("offset CreditCash record_type %zu\n", offsetof(CreditCash, record_type));
  std::printf("offset CreditCash seq_ms %zu\n", offsetof(CreditCash, seq_ms));
  std::printf("offset CreditCash seq_ord %zu\n", offsetof(CreditCash, seq_ord));
  std::printf("offset CreditCash timestamp_ns %zu\n", offsetof(CreditCash, timestamp_ns));
  std::printf("offset CreditCash client_order_id %zu\n", offsetof(CreditCash, client_order_id));
  std::printf("offset CreditCash user_id %zu\n", offsetof(CreditCash, user_id));
  std::printf("offset CreditCash amount_ticks %zu\n", offsetof(CreditCash, amount_ticks));
  std::printf("size OrderAccepted %zu\n", sizeof(OrderAccepted));
  std::printf("offset OrderAccepted schema_version %zu\n", offsetof(OrderAccepted, schema_version));
  std::printf("offset OrderAccepted record_type %zu\n", offsetof(OrderAccepted, record_type));
  std::printf("offset OrderAccepted seq_ms %zu\n", offsetof(OrderAccepted, seq_ms));
  std::printf("offset OrderAccepted seq_ord %zu\n", offsetof(OrderAccepted, seq_ord));
  std::printf("offset OrderAccepted timestamp_ns %zu\n", offsetof(OrderAccepted, timestamp_ns));
  std::printf("offset OrderAccepted order_id %zu\n", offsetof(OrderAccepted, order_id));
  std::printf("offset OrderAccepted client_order_id %zu\n", offsetof(OrderAccepted, client_order_id));
  std::printf("offset OrderAccepted user_id %zu\n", offsetof(OrderAccepted, user_id));
  std::printf("offset OrderAccepted price_ticks %zu\n", offsetof(OrderAccepted, price_ticks));
  std::printf("offset OrderAccepted qty %zu\n", offsetof(OrderAccepted, qty));
  std::printf("offset OrderAccepted symbol_id %zu\n", offsetof(OrderAccepted, symbol_id));
  std::printf("offset OrderAccepted side %zu\n", offsetof(OrderAccepted, side));
  std::printf("offset OrderAccepted tif %zu\n", offsetof(OrderAccepted, tif));
  std::printf("size OrderRejected %zu\n", sizeof(OrderRejected));
  std::printf("offset OrderRejected schema_version %zu\n", offsetof(OrderRejected, schema_version));
  std::printf("offset OrderRejected record_type %zu\n", offsetof(OrderRejected, record_type));
  std::printf("offset OrderRejected seq_ms %zu\n", offsetof(OrderRejected, seq_ms));
  std::printf("offset OrderRejected seq_ord %zu\n", offsetof(OrderRejected, seq_ord));
  std::printf("offset OrderRejected timestamp_ns %zu\n", offsetof(OrderRejected, timestamp_ns));
  std::printf("offset OrderRejected client_order_id %zu\n", offsetof(OrderRejected, client_order_id));
  std::printf("offset OrderRejected user_id %zu\n", offsetof(OrderRejected, user_id));
  std::printf("offset OrderRejected symbol_id %zu\n", offsetof(OrderRejected, symbol_id));
  std::printf("offset OrderRejected reason %zu\n", offsetof(OrderRejected, reason));
  std::printf("size Fill %zu\n", sizeof(Fill));
  std::printf("offset Fill schema_version %zu\n", offsetof(Fill, schema_version));
  std::printf("offset Fill record_type %zu\n", offsetof(Fill, record_type));
  std::printf("offset Fill seq_ms %zu\n", offsetof(Fill, seq_ms));
  std::printf("offset Fill seq_ord %zu\n", offsetof(Fill, seq_ord));
  std::printf("offset Fill timestamp_ns %zu\n", offsetof(Fill, timestamp_ns));
  std::printf("offset Fill maker_order_id %zu\n", offsetof(Fill, maker_order_id));
  std::printf("offset Fill taker_order_id %zu\n", offsetof(Fill, taker_order_id));
  std::printf("offset Fill maker_user_id %zu\n", offsetof(Fill, maker_user_id));
  std::printf("offset Fill taker_user_id %zu\n", offsetof(Fill, taker_user_id));
  std::printf("offset Fill price_ticks %zu\n", offsetof(Fill, price_ticks));
  std::printf("offset Fill qty %zu\n", offsetof(Fill, qty));
  std::printf("offset Fill symbol_id %zu\n", offsetof(Fill, symbol_id));
  std::printf("offset Fill aggressor_side %zu\n", offsetof(Fill, aggressor_side));
  std::printf("size OrderCancelled %zu\n", sizeof(OrderCancelled));
  std::printf("offset OrderCancelled schema_version %zu\n", offsetof(OrderCancelled, schema_version));
  std::printf("offset OrderCancelled record_type %zu\n", offsetof(OrderCancelled, record_type));
  std::printf("offset OrderCancelled seq_ms %zu\n", offsetof(OrderCancelled, seq_ms));
  std::printf("offset OrderCancelled seq_ord %zu\n", offsetof(OrderCancelled, seq_ord));
  std::printf("offset OrderCancelled timestamp_ns %zu\n", offsetof(OrderCancelled, timestamp_ns));
  std::printf("offset OrderCancelled order_id %zu\n", offsetof(OrderCancelled, order_id));
  std::printf("offset OrderCancelled client_order_id %zu\n", offsetof(OrderCancelled, client_order_id));
  std::printf("offset OrderCancelled user_id %zu\n", offsetof(OrderCancelled, user_id));
  std::printf("offset OrderCancelled remaining_qty %zu\n", offsetof(OrderCancelled, remaining_qty));
  std::printf("offset OrderCancelled symbol_id %zu\n", offsetof(OrderCancelled, symbol_id));
  std::printf("offset OrderCancelled reason %zu\n", offsetof(OrderCancelled, reason));
  std::printf("size BookChanged %zu\n", sizeof(BookChanged));
  std::printf("offset BookChanged schema_version %zu\n", offsetof(BookChanged, schema_version));
  std::printf("offset BookChanged record_type %zu\n", offsetof(BookChanged, record_type));
  std::printf("offset BookChanged seq_ms %zu\n", offsetof(BookChanged, seq_ms));
  std::printf("offset BookChanged seq_ord %zu\n", offsetof(BookChanged, seq_ord));
  std::printf("offset BookChanged timestamp_ns %zu\n", offsetof(BookChanged, timestamp_ns));
  std::printf("offset BookChanged price_ticks %zu\n", offsetof(BookChanged, price_ticks));
  std::printf("offset BookChanged qty_at_level %zu\n", offsetof(BookChanged, qty_at_level));
  std::printf("offset BookChanged symbol_id %zu\n", offsetof(BookChanged, symbol_id));
  std::printf("offset BookChanged side %zu\n", offsetof(BookChanged, side));
  std::printf("size AccountCreated %zu\n", sizeof(AccountCreated));
  std::printf("offset AccountCreated schema_version %zu\n", offsetof(AccountCreated, schema_version));
  std::printf("offset AccountCreated record_type %zu\n", offsetof(AccountCreated, record_type));
  std::printf("offset AccountCreated seq_ms %zu\n", offsetof(AccountCreated, seq_ms));
  std::printf("offset AccountCreated seq_ord %zu\n", offsetof(AccountCreated, seq_ord));
  std::printf("offset AccountCreated timestamp_ns %zu\n", offsetof(AccountCreated, timestamp_ns));
  std::printf("offset AccountCreated client_order_id %zu\n", offsetof(AccountCreated, client_order_id));
  std::printf("offset AccountCreated user_id %zu\n", offsetof(AccountCreated, user_id));
  std::printf("offset AccountCreated initial_cash_ticks %zu\n", offsetof(AccountCreated, initial_cash_ticks));
  std::printf("size CashCredited %zu\n", sizeof(CashCredited));
  std::printf("offset CashCredited schema_version %zu\n", offsetof(CashCredited, schema_version));
  std::printf("offset CashCredited record_type %zu\n", offsetof(CashCredited, record_type));
  std::printf("offset CashCredited seq_ms %zu\n", offsetof(CashCredited, seq_ms));
  std::printf("offset CashCredited seq_ord %zu\n", offsetof(CashCredited, seq_ord));
  std::printf("offset CashCredited timestamp_ns %zu\n", offsetof(CashCredited, timestamp_ns));
  std::printf("offset CashCredited client_order_id %zu\n", offsetof(CashCredited, client_order_id));
  std::printf("offset CashCredited user_id %zu\n", offsetof(CashCredited, user_id));
  std::printf("offset CashCredited amount_ticks %zu\n", offsetof(CashCredited, amount_ticks));
  return 0;
}
