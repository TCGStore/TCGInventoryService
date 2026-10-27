-- Reserves every item or none. Redis runs a script without interleaving other
-- commands, so two orders can never both take the last unit.
-- KEYS: processed-event marker, reservation hash, expiry zset, then one stock hash per item.
-- ARGV: order id, expires-at, marker ttl, low-stock threshold, then product id and quantity per item.
-- Returns {is_new, 'failed', unavailable ids...} or {is_new, 'reserved', product id, available...}
-- listing the products that just dropped below the threshold. A repeated event gets
-- the stored outcome back with is_new = 0 and no low-stock list.

local previous = redis.call('LRANGE', KEYS[1], 0, -1)
if #previous > 0 then
  table.insert(previous, 1, 0)
  return previous
end

local items = {}
local unavailable = {}
for i = 4, #KEYS do
  local arg = 5 + (i - 4) * 2
  local item = {
    key = KEYS[i],
    product_id = ARGV[arg],
    quantity = tonumber(ARGV[arg + 1]),
    available = tonumber(redis.call('HGET', KEYS[i], 'available') or '0'),
  }
  if item.available < item.quantity then
    table.insert(unavailable, item.product_id)
  end
  table.insert(items, item)
end

if #unavailable > 0 then
  local result = {1, 'failed'}
  redis.call('RPUSH', KEYS[1], 'failed')
  for _, product_id in ipairs(unavailable) do
    redis.call('RPUSH', KEYS[1], product_id)
    table.insert(result, product_id)
  end
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  return result
end

local threshold = tonumber(ARGV[4])
local result = {1, 'reserved'}
for _, item in ipairs(items) do
  local left = redis.call('HINCRBY', item.key, 'available', -item.quantity)
  redis.call('HINCRBY', item.key, 'reserved', item.quantity)
  redis.call('HSET', KEYS[2], item.product_id, item.quantity)
  if item.available >= threshold and left < threshold then
    table.insert(result, item.product_id)
    table.insert(result, left)
  end
end
redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
redis.call('RPUSH', KEYS[1], 'reserved')
redis.call('EXPIRE', KEYS[1], ARGV[3])
return result
