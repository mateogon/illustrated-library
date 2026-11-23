"""Narrative image prompts built from a card and a fixed palette of formal styles."""


PROMPT_VERSION = "anonymous-formal-v1"
PUBLIC_PROMPT_VERSION = "anonymous-public-palette-v1"
PUBLIC_COMPOSITION_VERSION = "anonymous-public-composition-v2"
PUBLIC_COMPOSITION = (
    " Si la ficha no indica cuántos elementos hay, encuadra el conjunto parcialmente para dejar su cantidad fuera de campo. "
    "Omite la utilería, las insignias, los mecanismos y las entregas de objetos que la ficha no describe. "
    "Conserva los gestos y la dirección de la acción. Evita gestos adicionales que alteren lo que sucede. "
    "Cuando un rasgo físico quede indeterminado, encuadra de cerca el motivo documentado y deja ese rasgo fuera de campo."
)
DEFAULT_STYLE = "Grafito y aguada gris sobre papel marfil, bordes de lápiz visibles y masas tonales claras."
PUBLIC_STYLES = {
    "treasure-island": ("Linograbado", "Linograbado en ultramar, ocre dorado y crema. Marcas de gubia cortas y masas de tinta claras y separadas."),
    "alices-adventures-in-wonderland": ("Pochoir", "Pochoir de contornos finos y capas mates de lila, frambuesa y verde menta sobre papel crema. Reservas amplias."),
    "the-time-machine": ("Litografía", "Litografía con grano de piedra, negro azulado, verde esmeralda desaturado y reflejos pálidos de cobre. Gamas de valor amplias."),
    "anne-of-green-gables": ("Acuarela y grafito", "Acuarela transparente y grafito sobre papel de grano fino. Verde salvia, coral claro y ultramar suave, con blancos reservados."),
    "a-christmas-carol": ("Monotipo", "Monotipo de carbón violáceo, ocre tostado y rosa grisáceo. Textura leve de rodillo y bordes de tinta graduados."),
    "the-hound-of-the-baskervilles": ("Aguafuerte", "Aguafuerte con trama espaciada y tinta azul pizarra sobre marfil. Contrastes de trama y reservas claras."),
    "sub-terra": ("Carboncillo y creta", "Carboncillo y creta sobre papel gris claro. Reservas luminosas y estructura de valores amplia, con pocos acentos de siena."),
    "leyendas-becquer": ("Serigrafía", "Serigrafía de tinta berenjena, verde jade y crema. Planos nítidos, trama tonal fina y ligera transparencia en los cruces."),
    "lazarillo": ("Punta de plata", "Punta de plata sobre papel color hueso, con líneas claras, pardo suave y toques de sombra fría. Textura fina y pausada."),
    "don-quijote": ("Temple y grafito", "Temple de acabado mate y grafito sobre papel cálido. Paleta de azul ultramar, sombra tostada y amarillo apagado. Bordes definidos y grandes valores claros."),
}


def make_prompt(slug, pick):
    return render(DEFAULT_STYLE, pick)


def make_public_prompt(work_id, pick, *, version=PUBLIC_PROMPT_VERSION):
    prompt = render(PUBLIC_STYLES[work_id][1], pick)
    if version == PUBLIC_PROMPT_VERSION:
        return prompt
    if version == PUBLIC_COMPOSITION_VERSION:
        return prompt + PUBLIC_COMPOSITION
    raise ValueError(f"Versión de prompt público desconocida: {version}")


def render(style, pick):
    return (
        "Ilustración narrativa horizontal 3:2. "
        f"Técnica visual: {style}\n"
        f"Escena: {pick['brief']}\nMotivo principal: {pick['core']['text']}\n"
        "Muestra un solo instante del motivo descrito. Mantén fuera de campo los elementos que "
        "no aparecen en la descripción. Si describe solo un lugar u objetos, úsalos como único "
        "sujeto. Se permiten rostros de figuras descritas. Reserva valores claros y separa el sujeto "
        "del fondo por el tono. Imagen a sangre, sin leyenda, título, firma ni marco."
    )
