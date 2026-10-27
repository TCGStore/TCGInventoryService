-- Ends a reservation: 'sell' drops the reserved units, 'release' returns them to available.
-- KEYS: reservation hash, expiry zset. ARGV: order id, 'sell' | 'release'.
-- Returns 0 if there was no reservation (already settled or expired), so repeats are harmless.
-- Stock keys come from the reservation itself; that works on a single Redis, not on Redis Cluster.

local reservation = redis.call('HGETALL', KEYS[1])
if #reservation == 0 then
  return 0
end

for i = 1, #reservation, 2 do
  local stock_key = 'stock:' .. reservation[i]
  local quantity = tonumber(reservation[i + 1])
  redis.call('HINCRBY', stock_key, 'reserved', -quantity)
  if ARGV[2] == 'release' then
    redis.call('HINCRBY', stock_key, 'available', quantity)
  end
end

redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
