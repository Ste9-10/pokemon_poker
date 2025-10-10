from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask, request, render_template, redirect, url_for, session, flash, send_file, jsonify
import os
import random
import threading
import time
from collections import defaultdict

app = Flask(__name__)
# Use environment variable for secret in production; fallback to default for local dev
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey_pokemonpoker_12345')

# Configurazione upload avatar e avatar predefiniti
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
AVATAR_UPLOAD_FOLDER = os.path.join('static', 'uploads', 'avatars')
DEFAULT_AVATARS_FOLDER = os.path.join('static', 'images', 'avatars')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
DEFAULT_AVATARS = ['pikachu.png', 'charizard.png', 'blastoise.png', 'venusaur.png', 'jigglypuff.png', 'mewtwo.png']

# Crea le cartelle se non esistono
os.makedirs(AVATAR_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DEFAULT_AVATARS_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = AVATAR_UPLOAD_FOLDER

# Funzioni di supporto
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_avatar_filename(username, filename):
    timestamp = int(datetime.now().timestamp())
    ext = filename.rsplit('.', 1)[1].lower()
    return f"{username}_{timestamp}.{ext}"

def is_valid_default_avatar(avatar):
    return avatar in DEFAULT_AVATARS and os.path.exists(os.path.join(DEFAULT_AVATARS_FOLDER, avatar))

# Database giocatori
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'credenziali.txt')

def load_credentials():
    try:
        with open(CREDENTIALS_FILE, "r") as f:
            credenziali = {}
            for line in f:
                line = line.strip()
                if not line or line.count(':') < 1:
                    continue

                parts = line.split(':')
                username = parts[0]
                password = parts[1]

                if len(parts) >= 3:
                    avatar = parts[2]
                    if (not os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], avatar))) and \
                       (not os.path.exists(os.path.join(DEFAULT_AVATARS_FOLDER, avatar))):
                        avatar = 'pikachu.png'  # Default avatar
                else:
                    avatar = 'pikachu.png'  # Default avatar

                credenziali[username] = {
                    'password': password,
                    'avatar': avatar
                }
            return credenziali
    except FileNotFoundError:
        return {}

def save_credentials(creds):
    with open(CREDENTIALS_FILE, "w") as f:
        for username, data in creds.items():
            line = f"{username}:{data['password']}:{data.get('avatar', 'pikachu.png')}\n"
            f.write(line)

# Sistema di gioco multiplayer
games = {}
rooms = {}  # Nuova struttura per le stanze
turn_timers = {}  # Timer per i turni

class PokemonDeck:
    suits = ['spades', 'hearts', 'clubs', 'diamonds']
    values = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

    def __init__(self):
        self.cards = [{'suit': s, 'value': v, 'pokemon': self.get_pokemon(s, v)} 
                     for s in self.suits for v in self.values]
        self.shuffle()

    def get_pokemon(self, suit, value):
        pokemon = {
            'spades': ['Charmander', 'Vulpix', 'Growlithe', 'Ponyta', 'Magmar', 'Arcanine', 'Rapidash', 'Charmeleon', 'Charizard', 'Moltres', 'Entei', 'Torchic', 'Blaziken'],
            'hearts': ['Squirtle', 'Psyduck', 'Poliwag', 'Tentacool', 'Magikarp', 'Staryu', 'Goldeen', 'Wartortle', 'Blastoise', 'Articuno', 'Suicune', 'Mudkip', 'Swampert'],
            'clubs': ['Bulbasaur', 'Oddish', 'Bellsprout', 'Exeggcute', 'Tangela', 'Weepinbell', 'Victreebel', 'Ivysaur', 'Venusaur', 'Celebi', 'Raikou', 'Treecko', 'Sceptile'],
            'diamonds': ['Pikachu', 'Magnemite', 'Voltorb', 'Electabuzz', 'Jolteon', 'Magneton', 'Electrode', 'Raichu', 'Zapdos', 'Ampharos', 'Manectric', 'Plusle', 'Minun']
        }
        idx = self.values.index(value) % len(pokemon[suit])
        return pokemon[suit][idx]

    def shuffle(self):
        random.shuffle(self.cards)

    def draw(self):
        return self.cards.pop() if self.cards else None

    def draw_multiple(self, count):
        cards = []
        for _ in range(count):
            card = self.draw()
            if card:
                cards.append(card)
        return cards

def evaluate_hand(hand):
    """Valuta la forza di una mano di poker italiano"""
    if not hand or len(hand) != 5:
        return 0

    values = [card['value'] for card in hand]
    suits = [card['suit'] for card in hand]

    # Converti i valori in numeri per il confronto
    value_map = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}
    numeric_values = sorted([value_map[v] for v in values], reverse=True)

    # Conta le occorrenze di ogni valore
    value_counts = {}
    for v in numeric_values:
        value_counts[v] = value_counts.get(v, 0) + 1

    counts = sorted(value_counts.values(), reverse=True)

    # Determina il tipo di mano (poker italiano)
    if len(set(suits)) == 1:  # Colore
        if numeric_values == [14, 13, 12, 11, 10]:  # Scala reale
            return 10000
        elif numeric_values == list(range(numeric_values[0], numeric_values[0] - 5, -1)):  # Scala colore
            return 9000 + numeric_values[0]
        else:  # Colore
            return 6000 + sum(numeric_values)

    # Scala
    if numeric_values == list(range(numeric_values[0], numeric_values[0] - 5, -1)):
        return 5000 + numeric_values[0]

    if counts == [4, 1]:  # Poker
        return 8000 + max([k for k, v in value_counts.items() if v == 4])
    elif counts == [3, 2]:  # Full
        return 7000 + max([k for k, v in value_counts.items() if v == 3])
    elif counts == [3, 1, 1]:  # Tris
        return 4000 + max([k for k, v in value_counts.items() if v == 3])
    elif counts == [2, 2, 1]:  # Doppia coppia
        pairs = [k for k, v in value_counts.items() if v == 2]
        return 3000 + max(pairs) * 100 + min(pairs)
    elif counts == [2, 1, 1, 1]:  # Coppia
        pair = [k for k, v in value_counts.items() if v == 2][0]
        return 2000 + pair
    else:  # Carta alta
        return 1000 + sum(numeric_values)

def get_hand_name(score):
    """Restituisce il nome della mano in base al punteggio"""
    if score >= 10000:
        return "Scala Reale"
    elif score >= 9000:
        return "Scala Colore"
    elif score >= 8000:
        return "Poker"
    elif score >= 7000:
        return "Full"
    elif score >= 6000:
        return "Colore"
    elif score >= 5000:
        return "Scala"
    elif score >= 4000:
        return "Tris"
    elif score >= 3000:
        return "Doppia Coppia"
    elif score >= 2000:
        return "Coppia"
    else:
        return "Carta Alta"

def start_turn_timer(game_id):
    """Avvia il timer per il turno corrente"""
    def timer_callback():
        time.sleep(60)  # 60 secondi
        if game_id in games and game_id in turn_timers:
            game = games[game_id]
            if not game.get('game_over', False) and game.get('phase') == 'cambio':
                # Passa automaticamente il turno
                current_player = game['players'][game['current_player']]
                game['players_ready'][current_player] = True

                # Controlla se tutti i giocatori sono pronti
                if all(game['players_ready'].values()):
                    evaluate_round(game)
                else:
                    # Passa al prossimo giocatore
                    game['current_player'] = (game['current_player'] + 1) % len(game['players'])
                    start_turn_timer(game_id)  # Avvia timer per il prossimo giocatore

            # Rimuovi il timer
            if game_id in turn_timers:
                del turn_timers[game_id]

    # Cancella il timer precedente se esiste
    if game_id in turn_timers:
        try:
            turn_timers[game_id].cancel()
        except Exception:
            pass

    # Avvia nuovo timer
    timer = threading.Timer(60.0, timer_callback)
    timer.start()
    turn_timers[game_id] = timer

# Rotta principale
@app.route('/')
def home():
    if 'user' in session:
        if 'avatar_selected' not in session:
            return redirect(url_for('change_avatar'))
        return redirect(url_for('lobby'))
    return render_template('index.html')

# Autenticazione
@app.route('/login', methods=['POST'])
def login():
    creds = load_credentials()
    username = request.form['username']
    password = request.form['password']

    if username in creds and creds[username]['password'] == password:
        session['user'] = username
        session.pop('avatar_selected', None)  # Forza la selezione dell'avatar
        return redirect(url_for('change_avatar'))
    else:
        flash('Credenziali errate!')
        return redirect(url_for('home'))

@app.route('/register', methods=['POST'])
def register():
    creds = load_credentials()
    username = request.form['username']
    password = request.form['password']

    if username in creds:
        flash('Nome utente già esistente!')
        return redirect(url_for('home'))

    # Assegna un avatar temporaneo
    creds[username] = {
        'password': password,
        'avatar': 'pikachu.png'
    }
    save_credentials(creds)
    session['user'] = username
    session.pop('avatar_selected', None)  # Forza la selezione dell'avatar
    return redirect(url_for('change_avatar'))

# Gestione Avatar
@app.route('/change_avatar', methods=['GET', 'POST'])
def change_avatar():
    if 'user' not in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        # Controlla se è stato selezionato un avatar predefinito
        if 'selected_avatar' in request.form:
            selected_avatar = request.form['selected_avatar']
            if is_valid_default_avatar(selected_avatar):
                username = session['user']
                creds = load_credentials()
                if username in creds:
                    creds[username]['avatar'] = selected_avatar
                    save_credentials(creds)
                    session['avatar_selected'] = True
                    return redirect(url_for('lobby'))

        # Controlla se è stato caricato un file
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file.filename != '' and allowed_file(file.filename):
                # Werkzeug's FileStorage doesn't expose content_length reliably for uploaded files here,
                # so we do a safer size check by seeking
                file.stream.seek(0, os.SEEK_END)
                filesize = file.stream.tell()
                file.stream.seek(0)
                if filesize > MAX_FILE_SIZE:
                    flash('File troppo grande (max 2MB)')
                    return redirect(url_for('change_avatar'))

                username = session['user']
                filename = generate_avatar_filename(username, secure_filename(file.filename))
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                try:
                    file.save(filepath)
                    creds = load_credentials()
                    if username in creds:
                        old_avatar = creds[username]['avatar']
                        if old_avatar != 'pikachu.png' and old_avatar not in DEFAULT_AVATARS:
                            old_path = os.path.join(app.config['UPLOAD_FOLDER'], old_avatar)
                            if os.path.exists(old_path):
                                os.remove(old_path)

                        creds[username]['avatar'] = filename
                        save_credentials(creds)
                        session['avatar_selected'] = True
                        return redirect(url_for('lobby'))
                except Exception as e:
                    flash("Errore durante il salvataggio dell'avatar")

        # Se nessuna opzione è stata selezionata
        flash('Seleziona un avatar predefinito o carica un\'immagine')

    return render_template('change_avatar.html', default_avatars=DEFAULT_AVATARS)

@app.route('/avatar/<filename>')
def get_avatar(filename):
    safe_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename))
    if not os.path.exists(safe_path):
        safe_path = os.path.join(DEFAULT_AVATARS_FOLDER, secure_filename(filename))
        if not os.path.exists(safe_path):
            safe_path = os.path.join(DEFAULT_AVATARS_FOLDER, 'pikachu.png')
    return send_file(safe_path)

# Nuova lobby per creare/unirsi alle stanze
@app.route('/lobby')
def lobby():
    if 'user' not in session or 'avatar_selected' not in session:
        return redirect(url_for('home'))

    user = session['user']
    creds = load_credentials()

    # Lista delle stanze disponibili
    available_rooms = []
    for room_id, room in rooms.items():
        if not room.get('game_started', False) and len(room['players']) < 10:
            available_rooms.append({
                'id': room_id,
                'name': room['name'],
                'creator': room['creator'],
                'players': len(room['players']),
                'max_players': 10
            })

    return render_template('lobby.html', 
                         user=user, 
                         creds=creds,
                         available_rooms=available_rooms)

@app.route('/create_room', methods=['POST'])
def create_room():
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    room_name = request.form.get('room_name', f"Stanza di {user}")
    room_id = str(random.randint(10000, 99999))

    rooms[room_id] = {
        'id': room_id,
        'name': room_name,
        'creator': user,
        'players': [user],
        'game_started': False,
        'created_at': datetime.now()
    }

    return redirect(url_for('room', room_id=room_id))

@app.route('/join_room/<room_id>')
def join_room(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']

    if room_id not in rooms:
        flash('Stanza non trovata!')
        return redirect(url_for('lobby'))

    room = rooms[room_id]

    if room.get('game_started', False):
        flash('La partita è già iniziata!')
        return redirect(url_for('lobby'))

    if len(room['players']) >= 10:
        flash('Stanza piena!')
        return redirect(url_for('lobby'))

    if user not in room['players']:
        room['players'].append(user)

    return redirect(url_for('room', room_id=room_id))

@app.route('/room/<room_id>')
def room(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    creds = load_credentials()

    if room_id not in rooms:
        flash('Stanza non trovata!')
        return redirect(url_for('lobby'))

    room = rooms[room_id]

    if user not in room['players']:
        return redirect(url_for('join_room', room_id=room_id))

    # Se il gioco è iniziato, reindirizza al gioco
    if room.get('game_started', False) and room_id in games:
        return redirect(url_for('game', game_id=room_id))

    return render_template('room.html', 
                         room=room, 
                         user=user, 
                         creds=creds)

@app.route('/start_game/<room_id>', methods=['POST'])
def start_game(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']

    if room_id not in rooms:
        return redirect(url_for('lobby'))

    room = rooms[room_id]

    # Solo il creatore può iniziare il gioco
    if user != room['creator']:
        flash('Solo il creatore può iniziare la partita!')
        return redirect(url_for('room', room_id=room_id))

    # Minimo 2 giocatori
    if len(room['players']) < 2:
        flash('Servono almeno 2 giocatori per iniziare!')
        return redirect(url_for('room', room_id=room_id))

    # Crea il gioco
    players = room['players'].copy()
    random.shuffle(players)  # Ordine casuale

    games[room_id] = {
        'players': players,
        'deck': PokemonDeck(),
        'hands': {p: [] for p in players},
        'current_player': 0,
        'round': 1,
        'max_rounds': 5,
        'game_over': False,
        'winner': None,
        'scores': {p: 0 for p in players},
        'round_wins': {p: 0 for p in players},
        'phase': 'cambio',
        'players_ready': {p: False for p in players},
        'round_winner': None,
        'round_winner_hand': None,
        'show_round_result': False,
        'turn_start_time': datetime.now(),
        'round_history': []  # Aggiunta cronologia round
    }

    # Distribuisci 5 carte iniziali a ogni giocatore
    for player in players:
        cards = games[room_id]['deck'].draw_multiple(5)
        games[room_id]['hands'][player] = cards

    room['game_started'] = True

    # Avvia il timer per il primo turno
    start_turn_timer(room_id)

    return redirect(url_for('game', game_id=room_id))

@app.route('/kick_player/<room_id>/<player_name>', methods=['POST'])
def kick_player(room_id, player_name):
    if 'user' not in session:
        return jsonify({'success': False, 'error': 'Non autenticato'}), 401

    user = session['user']

    if room_id not in rooms:
        return jsonify({'success': False, 'error': 'Stanza non trovata'}), 404

    room = rooms[room_id]

    # Solo il creatore può espellere
    if user != room['creator']:
        return jsonify({'success': False, 'error': 'Solo il creatore può espellere i giocatori'}), 403

    # Non può espellere se stesso
    if player_name == user:
        return jsonify({'success': False, 'error': 'Non puoi espellere te stesso'}), 400

    # Controlla se il giocatore è nella stanza
    if player_name not in room['players']:
        return jsonify({'success': False, 'error': 'Giocatore non trovato nella stanza'}), 404

    # Rimuovi il giocatore
    room['players'].remove(player_name)

    return jsonify({'success': True, 'message': f'{player_name} è stato espulso dalla stanza'})

# Gioco multiplayer
@app.route('/game/<game_id>')
def game(game_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']
    creds = load_credentials()

    if game_id not in games:
        return redirect(url_for('lobby'))

    game = games[game_id]

    if user not in game['players']:
        return redirect(url_for('lobby'))

    # Controlla se il gioco è finito
    if game.get('game_over', False):
        return render_template('game_over.html', 
                           game=game, 
                           game_id=game_id,
                           user=user,
                           creds=creds)

    # Calcola il tempo rimanente per il turno
    turn_time_left = 60
    if game.get('turn_start_time'):
        elapsed = (datetime.now() - game['turn_start_time']).total_seconds()
        turn_time_left = max(0, 60 - elapsed)

    return render_template('game.html', 
                       game=game, 
                       game_id=game_id,
                       user=user,
                       creds=creds,
                       turn_time_left=turn_time_left)

@app.route('/action/<action>', methods=['POST'])
def game_action(action):
    if 'user' not in session or 'avatar_selected' not in session:
        return redirect(url_for('home'))

    user = session['user']
    game = None
    game_id = None

    # Trova il gioco dell'utente
    for gid, g in games.items():
        if user in g['players']:
            game = g
            game_id = gid
            break

    if not game or game.get('game_over', False):
        return redirect(url_for('lobby'))

    # Gestisci le azioni in base alla fase
    if action == 'discard_selected':
        if game['phase'] == 'cambio' and game['players'][game['current_player']] == user:
            data = request.get_json()
            selected_indices = data.get('selected_cards', [])

            # Rimuovi le carte selezionate
            hand = game['hands'][user]
            new_hand = [card for i, card in enumerate(hand) if i not in selected_indices]

            # Pesca nuove carte
            cards_needed = len(selected_indices)
            new_cards = game['deck'].draw_multiple(cards_needed)
            new_hand.extend(new_cards)

            game['hands'][user] = new_hand
            game['players_ready'][user] = True

            # Cancella il timer del turno
            if game_id in turn_timers:
                try:
                    turn_timers[game_id].cancel()
                except Exception:
                    pass
                del turn_timers[game_id]

            # Controlla se tutti i giocatori sono pronti
            if all(game['players_ready'].values()):
                evaluate_round(game)
            else:
                # Passa al prossimo giocatore
                game['current_player'] = (game['current_player'] + 1) % len(game['players'])
                game['turn_start_time'] = datetime.now()
                start_turn_timer(game_id)

        return jsonify({'success': True})

    elif action == 'skip_discard':
        if game['phase'] == 'cambio' and game['players'][game['current_player']] == user:
            game['players_ready'][user] = True

            # Cancella il timer del turno
            if game_id in turn_timers:
                try:
                    turn_timers[game_id].cancel()
                except Exception:
                    pass
                del turn_timers[game_id]

            # Controlla se tutti i giocatori sono pronti
            if all(game['players_ready'].values()):
                evaluate_round(game)
            else:
                # Passa al prossimo giocatore
                game['current_player'] = (game['current_player'] + 1) % len(game['players'])
                game['turn_start_time'] = datetime.now()
                start_turn_timer(game_id)

        return jsonify({'success': True})

    elif action == 'next_round':
        if game['show_round_result']:
            start_next_round(game, game_id)

        return jsonify({'success': True})

    return redirect(url_for('game', game_id=game_id))

def evaluate_round(game):
    """Valuta il round e determina il vincitore"""
    # Calcola i punteggi per questo round
    round_scores = {}
    for player in game['players']:
        round_scores[player] = evaluate_hand(game['hands'][player])

    # Determina il vincitore del round
    round_winner = max(game['players'], key=lambda p: round_scores[p])
    game['round_winner'] = round_winner
    game['round_winner_hand'] = get_hand_name(round_scores[round_winner])
    game['round_wins'][round_winner] += 1
    game['show_round_result'] = True
    game['phase'] = 'round_result'

    # Aggiungi alla cronologia round
    game['round_history'].append({
        'round': game['round'],
        'winner': round_winner,
        'hand_name': game['round_winner_hand'],
        'score': round_scores[round_winner]
    })

def start_next_round(game, game_id):
    """Inizia il prossimo round o termina il gioco"""
    if game['round'] >= game['max_rounds']:
        # Fine del gioco dopo 5 round
        # Determina il vincitore finale (chi ha vinto più round)
        max_wins = max(game['round_wins'].values())
        winners = [p for p, wins in game['round_wins'].items() if wins == max_wins]

        if len(winners) == 1:
            final_winner = winners[0]
        else:
            # In caso di pareggio, vince chi ha la mano migliore nell'ultimo round
            final_scores = {p: evaluate_hand(game['hands'][p]) for p in winners}
            final_winner = max(winners, key=lambda p: final_scores[p])

        # Calcola i punteggi finali per la visualizzazione
        for player in game['players']:
            game['scores'][player] = evaluate_hand(game['hands'][player])

        game['winner'] = final_winner
        game['game_over'] = True

        # Cancella eventuali timer
        if game_id in turn_timers:
            try:
                turn_timers[game_id].cancel()
            except Exception:
                pass
            del turn_timers[game_id]
    else:
        # Prossimo round
        game['round'] += 1
        game['phase'] = 'cambio'
        game['current_player'] = 0
        game['players_ready'] = {p: False for p in game['players']}
        game['show_round_result'] = False
        game['round_winner'] = None
        game['round_winner_hand'] = None
        game['turn_start_time'] = datetime.now()

        # Nuove carte per tutti i giocatori
        game['deck'] = PokemonDeck()  # Nuovo mazzo
        for player in game['players']:
            cards = game['deck'].draw_multiple(5)
            game['hands'][player] = cards

        # Avvia timer per il nuovo round
        start_turn_timer(game_id)

@app.route('/game_status/<game_id>')
def game_status(game_id):
    """API endpoint per ottenere lo stato del gioco (per auto-refresh)"""
    if game_id in games:
        game = games[game_id]

        # Calcola il tempo rimanente per il turno
        turn_time_left = 60
        if game.get('turn_start_time'):
            elapsed = (datetime.now() - game['turn_start_time']).total_seconds()
            turn_time_left = max(0, 60 - elapsed)

        return jsonify({
            'game_over': game.get('game_over', False),
            'current_player': game.get('current_player', 0),
            'round': game.get('round', 1),
            'phase': game.get('phase', 'cambio'),
            'winner': game.get('winner', None),
            'show_round_result': game.get('show_round_result', False),
            'round_winner': game.get('round_winner', None),
            'turn_time_left': turn_time_left
        })
    return jsonify({'error': 'Game not found'}), 404

@app.route('/room_status/<room_id>')
def room_status(room_id):
    """API endpoint per ottenere lo stato della stanza"""
    if room_id in rooms:
        room = rooms[room_id]
        return jsonify({
            'players': room['players'],
            'game_started': room.get('game_started', False)
        })
    return jsonify({'error': 'Room not found'}), 404

@app.route('/leave_room/<room_id>', methods=['POST'])
def leave_room(room_id):
    if 'user' not in session:
        return redirect(url_for('home'))

    user = session['user']

    if room_id in rooms:
        room = rooms[room_id]
        if user in room['players']:
            room['players'].remove(user)

            # Se era il creatore e ci sono altri giocatori, passa la creazione al primo
            if user == room['creator'] and room['players']:
                room['creator'] = room['players'][0]

            # Se non ci sono più giocatori, elimina la stanza
            if not room['players']:
                del rooms[room_id]
                if room_id in games:
                    del games[room_id]
                if room_id in turn_timers:
                    try:
                        turn_timers[room_id].cancel()
                    except Exception:
                        pass
                    del turn_timers[room_id]

    return redirect(url_for('lobby'))

@app.route('/logout')
def logout():
    user = session.get('user')

    # Rimuovi l'utente da eventuali stanze e giochi
    if user:
        for room_id, room in list(rooms.items()):
            if user in room['players']:
                room['players'].remove(user)

                # Se era il creatore e ci sono altri giocatori, passa la creazione al primo
                if user == room['creator'] and room['players']:
                    room['creator'] = room['players'][0]

                # Se non ci sono più giocatori, elimina la stanza
                if not room['players']:
                    del rooms[room_id]
                    if room_id in games:
                        del games[room_id]
                    if room_id in turn_timers:
                        try:
                            turn_timers[room_id].cancel()
                        except Exception:
                            pass
                        del turn_timers[room_id]

    session.pop('user', None)
    session.pop('avatar_selected', None)
    return redirect(url_for('home'))

def find_available_port():
    """Trova una porta disponibile (utile per esecuzione locale)"""
    import socket
    ports_to_try = [5001, 5002, 5003, 8000, 8080, 3000]

    for port in ports_to_try:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue

    # Se nessuna porta è disponibile, usa una porta casuale
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

if __name__ == '__main__':
    port = find_available_port()
    print(f"🚀 Starting Pokemon Poker server on port {port}")
    print(f"🌐 Access the game at: http://localhost:{port}")
    app.run(debug=True, host='0.0.0.0', port=port)
