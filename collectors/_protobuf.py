"""A minimal schema-less protobuf reader.

Enough to walk the wire format of blobs whose .proto we do not have: fields
come back as (number, wire type, value), with length-delimited values left as
bytes so the caller decides whether they are strings or nested messages.
"""


def _varint(buf, i):
  result = shift = 0
  while True:
    byte = buf[i]
    i += 1
    result |= (byte & 0x7F) << shift
    shift += 7
    if byte < 0x80:
      return result, i


def fields(buf):
  """Every top-level field of a message; [] when buf is not valid protobuf."""
  out, i = [], 0
  try:
    while i < len(buf):
      key, i = _varint(buf, i)
      number, wire = key >> 3, key & 7
      if number == 0:
        return []
      if wire == 0:
        value, i = _varint(buf, i)
      elif wire == 1:
        value, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
      elif wire == 5:
        value, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
      elif wire == 2:
        size, i = _varint(buf, i)
        value, i = bytes(buf[i:i + size]), i + size
      else:
        return []
      out.append((number, wire, value))
  except IndexError:
    return []
  return out


def get(buf, *path):
  """The first value at a field-number path, e.g. get(blob, 1, 4); None if absent."""
  for number, wire, value in fields(buf or b""):
    if number != path[0]:
      continue
    if len(path) == 1:
      return value
    if wire == 2:
      found = get(value, *path[1:])
      if found is not None:
        return found
  return None


def all_of(buf, number):
  """Every value of a repeated field."""
  return [value for n, _, value in fields(buf or b"") if n == number]


def ints(buf):
  """A message's varint fields as {number: value}."""
  return {n: v for n, w, v in fields(buf or b"") if w == 0}
