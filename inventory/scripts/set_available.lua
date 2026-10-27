-- KEYS: stock hash. ARGV: new available units.
-- Returns {previous available, reserved}; reserved is left untouched.

local previous = tonumber(redis.call('HGET', KEYS[1], 'available') or '0')
redis.call('HSET', KEYS[1], 'available', ARGV[1])
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved') or '0')
return {previous, reserved}
