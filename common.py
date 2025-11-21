# common.py
# Constantes y utilidades compartidas entre servidor y cliente.

import json
import struct

# protocolo: length-prefix (4 bytes) + JSON payload (utf-8)

def send_msg(sock, obj):
    """
    Envía un objeto serializable en JSON con prefijo de longitud.
    """
    data = json.dumps(obj).encode('utf-8')
    length = struct.pack('!I', len(data))
    sock.sendall(length + data)

def recv_msg(sock):
    """
    Recibe el mensaje con prefijo de longitud.
    Devuelve el objeto decodificado o None si socket cerrado/EOF.
    """
    # recibir 4 bytes
    raw_len = recvall(sock, 4)
    if not raw_len:
        return None
    msg_len = struct.unpack('!I', raw_len)[0]
    data = recvall(sock, msg_len)
    if not data:
        return None
    return json.loads(data.decode('utf-8'))

def recvall(sock, n):
    data = b''
    while len(data) < n:
        try:
            packet = sock.recv(n - len(data))
        except ConnectionResetError:
            return None
        if not packet:
            return None
        data += packet
    return data

# Mensajes tipo
# client -> server:
# { "type": "join", "name": "Jugador1" }
# { "type": "place_tower", "x": 120, "y": 200, "tower_type": "basic" }
#
# server -> client:
# { "type": "snapshot", "state": {...} }
# { "type": "welcome", "player_id": 1 }
#
# El "state" contiene: enemies list, towers list, bullets list, players info
