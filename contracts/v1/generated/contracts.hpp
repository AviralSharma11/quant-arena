// GENERATED FILE — DO NOT EDIT.
//
// Source:     contracts/v1/schema.toml
// Source sha: 18bb7c3798cd8f892f94bb249bb840544e41f144139c46ded9b20c4cc29ea375
// Regenerate: python contracts/v1/generate.py
//
// Hand-editing this file reintroduces exactly the C++/Python drift the generator exists to
// prevent. contracts/v1/tests/test_generated_is_current.py fails if you do.

#pragma once

#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace quant_arena::contracts::v1 {

inline constexpr std::uint16_t SCHEMA_VERSION = 1;

// A record's seq is the Redis stream id, which does not exist until XADD returns.
// Producers write SEQ_UNASSIGNED; consumers fill it in from the message id on read.
inline constexpr std::uint64_t SEQ_UNASSIGNED = 0;

// Record discriminator. Synthesised from the record list.
enum class RecordType : std::uint16_t {
  SUBMIT_ORDER = 1,  // SubmitOrder
  CANCEL_ORDER = 2,  // CancelOrder
  CREATE_ACCOUNT = 3,  // CreateAccount
  CREDIT_CASH = 4,  // CreditCash
  ORDER_ACCEPTED = 10,  // OrderAccepted
  ORDER_REJECTED = 11,  // OrderRejected
  FILL = 12,  // Fill
  ORDER_CANCELLED = 13,  // OrderCancelled
  BOOK_CHANGED = 14,  // BookChanged
  ACCOUNT_CREATED = 15,  // AccountCreated
  CASH_CREDITED = 16,  // CashCredited
};

// Order side. Also used for Fill.aggressor_side.
enum class Side : std::uint8_t {
  BUY = 1,
  SELL = 2,
};

// Time in force. GTC and IOC only — nothing else is meaningful without a session clock.
enum class Tif : std::uint8_t {
  GTC = 1,  // Good till cancelled.
  IOC = 2,  // Immediate or cancel; any unfilled remainder is cancelled.
};

// Why an order left the book. Distinguishes user-initiated from expiry.
enum class CancelReason : std::uint8_t {
  USER_REQUESTED = 1,
  IOC_EXPIRED = 2,  // IOC remainder cancelled after aggressing.
};

// Closed set. Adding a reason later is a schema change (Open Issue 016 section 10), so the full
// set is enumerated now even though the risk checks that raise most of them are week 3.
enum class RejectReason : std::uint16_t {
  UNKNOWN_SYMBOL = 1,
  INVALID_PRICE = 2,
  INVALID_QUANTITY = 3,
  INVALID_SIDE = 4,
  INVALID_TIF = 5,
  INSUFFICIENT_CASH = 6,
  INSUFFICIENT_POSITION = 7,
  UNKNOWN_ORDER = 8,  // Cancel referenced a client_order_id with no live order.
  NOT_ORDER_OWNER = 9,  // Cancel referenced another user's order.
  DUPLICATE_CLIENT_ORDER_ID = 10,
  EXCHANGE_HALTED = 11,
  ACCOUNT_NOT_FOUND = 12,
  ACCOUNT_ALREADY_EXISTS = 13,
};

// Packed, little-endian, no padding anywhere. This is what makes sizeof() here equal
// struct.calcsize() of the '<'-prefixed format string in contracts.py.
#pragma pack(push, 1)

// A new order. Matched by the engine.
// Direction: inbound. record_type = 1.
struct SubmitOrder {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;  // Mandatory idempotency key, unique per user.
  std::uint64_t user_id;
  std::int64_t price_ticks;
  std::int64_t qty;
  std::int16_t symbol_id;
  Side side;
  Tif tif;
};
static_assert(sizeof(SubmitOrder) == 64, "SubmitOrder must be 64 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<SubmitOrder>, "SubmitOrder must be trivially copyable");
static_assert(offsetof(SubmitOrder, schema_version) == 0, "SubmitOrder.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, record_type) == 2, "SubmitOrder.record_type moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, seq_ms) == 4, "SubmitOrder.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, seq_ord) == 12, "SubmitOrder.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, timestamp_ns) == 20, "SubmitOrder.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, client_order_id) == 28, "SubmitOrder.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, user_id) == 36, "SubmitOrder.user_id moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, price_ticks) == 44, "SubmitOrder.price_ticks moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, qty) == 52, "SubmitOrder.qty moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, symbol_id) == 60, "SubmitOrder.symbol_id moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, side) == 62, "SubmitOrder.side moved — regenerate from schema.toml");
static_assert(offsetof(SubmitOrder, tif) == 63, "SubmitOrder.tif moved — regenerate from schema.toml");

// Cancel by client order id — a client can cancel an order whose acknowledgement it never
// received.
// Direction: inbound. record_type = 2.
struct CancelOrder {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;  // Idempotency key of the cancel itself.
  std::uint64_t user_id;
  std::uint64_t target_client_order_id;  // The order being cancelled.
};
static_assert(sizeof(CancelOrder) == 52, "CancelOrder must be 52 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<CancelOrder>, "CancelOrder must be trivially copyable");
static_assert(offsetof(CancelOrder, schema_version) == 0, "CancelOrder.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, record_type) == 2, "CancelOrder.record_type moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, seq_ms) == 4, "CancelOrder.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, seq_ord) == 12, "CancelOrder.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, timestamp_ns) == 20, "CancelOrder.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, client_order_id) == 28, "CancelOrder.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, user_id) == 36, "CancelOrder.user_id moved — regenerate from schema.toml");
static_assert(offsetof(CancelOrder, target_client_order_id) == 44, "CancelOrder.target_client_order_id moved — regenerate from schema.toml");

// Forwarded by the engine untouched — the engine is money-blind.
// Direction: inbound. record_type = 3.
struct CreateAccount {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;  // Idempotency key.
  std::uint64_t user_id;
};
static_assert(sizeof(CreateAccount) == 44, "CreateAccount must be 44 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<CreateAccount>, "CreateAccount must be trivially copyable");
static_assert(offsetof(CreateAccount, schema_version) == 0, "CreateAccount.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(CreateAccount, record_type) == 2, "CreateAccount.record_type moved — regenerate from schema.toml");
static_assert(offsetof(CreateAccount, seq_ms) == 4, "CreateAccount.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(CreateAccount, seq_ord) == 12, "CreateAccount.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(CreateAccount, timestamp_ns) == 20, "CreateAccount.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(CreateAccount, client_order_id) == 28, "CreateAccount.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(CreateAccount, user_id) == 36, "CreateAccount.user_id moved — regenerate from schema.toml");

// Forwarded by the engine untouched.
// Direction: inbound. record_type = 4.
struct CreditCash {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;  // Idempotency key.
  std::uint64_t user_id;
  std::int64_t amount_ticks;
};
static_assert(sizeof(CreditCash) == 52, "CreditCash must be 52 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<CreditCash>, "CreditCash must be trivially copyable");
static_assert(offsetof(CreditCash, schema_version) == 0, "CreditCash.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, record_type) == 2, "CreditCash.record_type moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, seq_ms) == 4, "CreditCash.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, seq_ord) == 12, "CreditCash.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, timestamp_ns) == 20, "CreditCash.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, client_order_id) == 28, "CreditCash.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, user_id) == 36, "CreditCash.user_id moved — regenerate from schema.toml");
static_assert(offsetof(CreditCash, amount_ticks) == 44, "CreditCash.amount_ticks moved — regenerate from schema.toml");

// Order is live in the book. Carries price/qty/side/symbol because there are no snapshots (Open
// Issue 018 section 13.1): the ledger rebuilds open orders by replaying the OUTBOUND stream
// alone, and without these it would have to join against the inbound stream.
// Direction: outbound. record_type = 10.
struct OrderAccepted {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t order_id;  // Engine-assigned.
  std::uint64_t client_order_id;
  std::uint64_t user_id;
  std::int64_t price_ticks;
  std::int64_t qty;  // Quantity accepted onto the book.
  std::int16_t symbol_id;
  Side side;
  Tif tif;
};
static_assert(sizeof(OrderAccepted) == 72, "OrderAccepted must be 72 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<OrderAccepted>, "OrderAccepted must be trivially copyable");
static_assert(offsetof(OrderAccepted, schema_version) == 0, "OrderAccepted.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, record_type) == 2, "OrderAccepted.record_type moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, seq_ms) == 4, "OrderAccepted.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, seq_ord) == 12, "OrderAccepted.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, timestamp_ns) == 20, "OrderAccepted.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, order_id) == 28, "OrderAccepted.order_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, client_order_id) == 36, "OrderAccepted.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, user_id) == 44, "OrderAccepted.user_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, price_ticks) == 52, "OrderAccepted.price_ticks moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, qty) == 60, "OrderAccepted.qty moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, symbol_id) == 68, "OrderAccepted.symbol_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, side) == 70, "OrderAccepted.side moved — regenerate from schema.toml");
static_assert(offsetof(OrderAccepted, tif) == 71, "OrderAccepted.tif moved — regenerate from schema.toml");

// Order never reached the book. No order_id was assigned.
// Direction: outbound. record_type = 11.
struct OrderRejected {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;
  std::uint64_t user_id;
  std::int16_t symbol_id;
  RejectReason reason;
};
static_assert(sizeof(OrderRejected) == 48, "OrderRejected must be 48 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<OrderRejected>, "OrderRejected must be trivially copyable");
static_assert(offsetof(OrderRejected, schema_version) == 0, "OrderRejected.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, record_type) == 2, "OrderRejected.record_type moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, seq_ms) == 4, "OrderRejected.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, seq_ord) == 12, "OrderRejected.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, timestamp_ns) == 20, "OrderRejected.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, client_order_id) == 28, "OrderRejected.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, user_id) == 36, "OrderRejected.user_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, symbol_id) == 44, "OrderRejected.symbol_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderRejected, reason) == 46, "OrderRejected.reason moved — regenerate from schema.toml");

// One trade. Both sides are named. aggressor_side is required for maker/taker fees (Open Issue
// 011 section 11.2) and cannot be derived after the fact.
// Direction: outbound. record_type = 12.
struct Fill {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t maker_order_id;
  std::uint64_t taker_order_id;
  std::uint64_t maker_user_id;
  std::uint64_t taker_user_id;
  std::int64_t price_ticks;  // Always the resting (maker) price — price-time priority.
  std::int64_t qty;
  std::int16_t symbol_id;
  Side aggressor_side;
};
static_assert(sizeof(Fill) == 79, "Fill must be 79 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<Fill>, "Fill must be trivially copyable");
static_assert(offsetof(Fill, schema_version) == 0, "Fill.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(Fill, record_type) == 2, "Fill.record_type moved — regenerate from schema.toml");
static_assert(offsetof(Fill, seq_ms) == 4, "Fill.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(Fill, seq_ord) == 12, "Fill.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(Fill, timestamp_ns) == 20, "Fill.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(Fill, maker_order_id) == 28, "Fill.maker_order_id moved — regenerate from schema.toml");
static_assert(offsetof(Fill, taker_order_id) == 36, "Fill.taker_order_id moved — regenerate from schema.toml");
static_assert(offsetof(Fill, maker_user_id) == 44, "Fill.maker_user_id moved — regenerate from schema.toml");
static_assert(offsetof(Fill, taker_user_id) == 52, "Fill.taker_user_id moved — regenerate from schema.toml");
static_assert(offsetof(Fill, price_ticks) == 60, "Fill.price_ticks moved — regenerate from schema.toml");
static_assert(offsetof(Fill, qty) == 68, "Fill.qty moved — regenerate from schema.toml");
static_assert(offsetof(Fill, symbol_id) == 76, "Fill.symbol_id moved — regenerate from schema.toml");
static_assert(offsetof(Fill, aggressor_side) == 78, "Fill.aggressor_side moved — regenerate from schema.toml");

// Order left the book without being fully filled.
// Direction: outbound. record_type = 13.
struct OrderCancelled {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t order_id;
  std::uint64_t client_order_id;
  std::uint64_t user_id;
  std::int64_t remaining_qty;  // Quantity removed from the book.
  std::int16_t symbol_id;
  CancelReason reason;
};
static_assert(sizeof(OrderCancelled) == 63, "OrderCancelled must be 63 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<OrderCancelled>, "OrderCancelled must be trivially copyable");
static_assert(offsetof(OrderCancelled, schema_version) == 0, "OrderCancelled.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, record_type) == 2, "OrderCancelled.record_type moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, seq_ms) == 4, "OrderCancelled.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, seq_ord) == 12, "OrderCancelled.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, timestamp_ns) == 20, "OrderCancelled.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, order_id) == 28, "OrderCancelled.order_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, client_order_id) == 36, "OrderCancelled.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, user_id) == 44, "OrderCancelled.user_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, remaining_qty) == 52, "OrderCancelled.remaining_qty moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, symbol_id) == 60, "OrderCancelled.symbol_id moved — regenerate from schema.toml");
static_assert(offsetof(OrderCancelled, reason) == 62, "OrderCancelled.reason moved — regenerate from schema.toml");

// One aggregated price level, after the change. A fixed-size POD record cannot carry a variable
// list of levels, so the engine emits per-level updates and fan-out maintains its own book from
// them; the complete-L2-snapshot rule of Open Issue 006 governs the browser wire, not this
// record. qty_at_level = 0 means the level is now empty.
// Direction: outbound. record_type = 14.
struct BookChanged {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::int64_t price_ticks;
  std::int64_t qty_at_level;  // Aggregate resting quantity at this price after the change. 0 = level removed.
  std::int16_t symbol_id;
  Side side;
};
static_assert(sizeof(BookChanged) == 47, "BookChanged must be 47 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<BookChanged>, "BookChanged must be trivially copyable");
static_assert(offsetof(BookChanged, schema_version) == 0, "BookChanged.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, record_type) == 2, "BookChanged.record_type moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, seq_ms) == 4, "BookChanged.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, seq_ord) == 12, "BookChanged.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, timestamp_ns) == 20, "BookChanged.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, price_ticks) == 28, "BookChanged.price_ticks moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, qty_at_level) == 36, "BookChanged.qty_at_level moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, symbol_id) == 44, "BookChanged.symbol_id moved — regenerate from schema.toml");
static_assert(offsetof(BookChanged, side) == 46, "BookChanged.side moved — regenerate from schema.toml");

// Forwarded record, now sequenced.
// Direction: outbound. record_type = 15.
struct AccountCreated {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;
  std::uint64_t user_id;
  std::int64_t initial_cash_ticks;
};
static_assert(sizeof(AccountCreated) == 52, "AccountCreated must be 52 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<AccountCreated>, "AccountCreated must be trivially copyable");
static_assert(offsetof(AccountCreated, schema_version) == 0, "AccountCreated.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, record_type) == 2, "AccountCreated.record_type moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, seq_ms) == 4, "AccountCreated.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, seq_ord) == 12, "AccountCreated.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, timestamp_ns) == 20, "AccountCreated.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, client_order_id) == 28, "AccountCreated.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, user_id) == 36, "AccountCreated.user_id moved — regenerate from schema.toml");
static_assert(offsetof(AccountCreated, initial_cash_ticks) == 44, "AccountCreated.initial_cash_ticks moved — regenerate from schema.toml");

// Forwarded record, now sequenced. The engine is money-blind and does not track balances.
// Direction: outbound. record_type = 16.
struct CashCredited {
  std::uint16_t schema_version;  // Version of this schema. Consumers accept the current version only.
  RecordType record_type;  // Discriminator. Fixed offset across every record.
  std::uint64_t seq_ms;  // Redis stream id, millisecond part. 0 until assigned on read.
  std::uint64_t seq_ord;  // Redis stream id, ordinal part. 0 until assigned on read.
  std::int64_t timestamp_ns;  // Gateway-assigned wall clock, nanoseconds.
  std::uint64_t client_order_id;
  std::uint64_t user_id;
  std::int64_t amount_ticks;
};
static_assert(sizeof(CashCredited) == 52, "CashCredited must be 52 bytes — regenerate from schema.toml");
static_assert(std::is_trivially_copyable_v<CashCredited>, "CashCredited must be trivially copyable");
static_assert(offsetof(CashCredited, schema_version) == 0, "CashCredited.schema_version moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, record_type) == 2, "CashCredited.record_type moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, seq_ms) == 4, "CashCredited.seq_ms moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, seq_ord) == 12, "CashCredited.seq_ord moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, timestamp_ns) == 20, "CashCredited.timestamp_ns moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, client_order_id) == 28, "CashCredited.client_order_id moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, user_id) == 36, "CashCredited.user_id moved — regenerate from schema.toml");
static_assert(offsetof(CashCredited, amount_ticks) == 44, "CashCredited.amount_ticks moved — regenerate from schema.toml");

#pragma pack(pop)

}  // namespace quant_arena::contracts::v1
