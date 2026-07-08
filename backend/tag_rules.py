# backend/tag_rules.py — controlled tag vocabulary + deterministic alias mapping.
# Used by the one-time normalization job AND (future) at import time to prevent
# tag sprawl from recurring. "vegetable"/"vegetables" intentionally dropped (too
# vague to filter on — confirmed by David). "recipe"/"technique" dropped as TAGS
# because they duplicate the `subtitle` TYPE field the app's sections rely on.

CUISINE = {"asian","chinese","japanese","korean","thai","vietnamese","indonesian",
    "italian","french","spanish","mexican","american","swiss","german",
    "mediterranean","moroccan","indian","middle-eastern","south-african"}
PROTEIN = {"beef","pork","chicken","poultry","lamb","fish","seafood","shrimp",
    "egg","tofu","beans","vegetarian","vegan"}
METHOD = {"wok","stir-fry","grill","roast","braise","fry","deep-fry","steam",
    "sous-vide","pressure-cooker","microwave","oven","stovetop","cast-iron",
    "cook-expert","no-cook","blanch"}
MEAL = {"breakfast","lunch","dinner","side","soup","salad","noodles","rice",
    "pasta","sauce","condiment","marinade","dessert","snack","sandwich","dip"}
EFFORT = {"quick","weeknight","make-ahead","batch-cook","one-pan","hands-off",
    "budget","comfort-food","healthy"}
FLAVOR = {"spicy","umami","sichuan"}
TECHTYPE = {"knife-skills","heat-method","prep","sauce-making","foundational"}

VOCAB = CUISINE|PROTEIN|METHOD|MEAL|EFFORT|FLAVOR|TECHTYPE

ALIAS: dict[str, list[str]] = {}
def _m(clean, *raws):
    vals = clean if isinstance(clean, list) else [clean]
    for r in raws:
        ALIAS[r.lower()] = vals

# ── explicit drops (never map to anything, even though they're common) ──
DROP = {"recipe", "technique", "vegetable", "vegetables", "vegetable preparation",
        "vegetable prep", "vegetable side", "vegetable side dish", "main course",
        "preparation"}

# cuisine
_m("asian","asian","asian cuisine","asian cooking","asian-inspired","asian-fusion","east asian","asian greens","southeast asian","asian cooking")
_m("chinese","chinese","chinese cooking","chinese cuisine","chinese american","cantonese","hunan","shanghai","guangdong","shunde","chongqing","beijing","guangzhou","canton","hong kong","northern chinese","china","china time-honored brand","chinese broccoli","peking duck","yangzhou-style")
_m(["chinese","sichuan"],"sichuan","málà","numbing","fish-fragrant style","yuxiang","hupi qingjiao","dan dan noodles","numbing hot")
_m("japanese","japanese","japanese-inspired","japanese-fusion","japan","donburi","don","mentsuyu","tsuyu","mirin","sake")
_m("korean","korean","banchan","kimchi","dduk","nian gao")
_m("thai","thai","thai-inspired","pad thai")
_m("vietnamese","vietnamese","vietnamese american")
_m("indonesian","indonesian")
_m("italian","italian","italian-inspired","tuscan")
_m("french","french","french technique","french cuisine","classical french cuisine","monter au beurre","french onion soup")
_m("spanish","spanish","spanish technique")
_m("mexican","mexican","southwestern","tomatillo")
_m("american","american","american cuisine","american-style","diner-style","pub-style","texas style","san francisco style")
_m("swiss","swiss","rösti")
_m("german","german","bratwurst","uyghur")
_m("mediterranean","mediterranean")
_m("moroccan","moroccan","north african","harissa")
_m("south-african","south-african")

# protein
_m("beef","beef","steak","steaks","flank steak","flap meat","hanger steak","tri-tip","tenderloin","ground beef","burger","burgers","meatballs")
_m("pork","pork","bacon","ham","sausage","sausages","chorizo","sausage seasoning","ribs","chops","prosciutto","spam","pepperoni")
_m("chicken","chicken","poached chicken")
_m("poultry","poultry","turkey")
_m("lamb","lamb","lamb compatible")
_m("fish","fish","salmon","filleting","salted fish","sashimi-quality")
_m("seafood","seafood","shrimp","clams","crab","lobster sauce","poached shrimp","deveining")
_m("egg","egg","eggs","fried eggs","fried egg","scrambled","soft-boiled","hard-boiled","poached eggs","omelet","sunny-side-up","meringue","egg whites","egg white meringue","eggs benedict")
_m("tofu","tofu")
_m("beans","beans","black beans","white beans","canned beans","chickpeas","legumes","lentils","fermented beans")
_m("vegetarian","vegetarian","vegetarian-adaptable","vegetarian-optional","vegetarian optional","vegetarian adaptable","vegetarian-friendly","meat-free")
_m("vegan","vegan","vegan-adaptable","vegan-optional")

# method
_m("wok","wok","wok cooking","wok hei","one-wok","one-wok meal","stir-frying")
_m("stir-fry","stir-fry","stir-fried","stir-fry preparation")
_m("grill","grill","grilling","grilled","charcoal grill","charcoal grilling","gas grill","two-zone cooking","two-stage cooking","barbecue","bbq","charcoal","grilled meats")
_m("roast","roast","roasting","roasted","oven-roasted","pan-roasted","pan-roasting")
_m("braise","braise","braising","braised","pan-braising","stewing","stew")
_m("fry","fry","pan-fried","panfried","pan-seared","pan-searing","searing","seared","sautéing","sauté","sautéed","skillet","panfrying","pan-cooking","browning","blowtorch","torch method","torch charring","butter-basted","butter basting","basting")
_m("deep-fry","deep-fry","deep-frying","deep-fried","frying","fried","dry-frying","dry-fried","deep frying")
_m("steam","steam","steaming","steamed")
_m("blanch","blanch","blanching","parboiling","parboil","parcooking")
_m("sous-vide","sous-vide","sous vide","sous vide alternative","water-bath cooking","cooler cooking","cooler-cooking","cooler-cooked","low-temperature cooking","low temperature cooking","vacuum-sealing")
_m("pressure-cooker","pressure cooker")
_m("microwave","microwave","microwave cooking")
_m("oven","oven","oven cooking","baked","baking","broil","broiled","broiling","oven-finished","stovetop-to-oven","gratin")
_m("stovetop","stovetop","stovetop cooking","indoor cooking")
_m("cast-iron","cast-iron","cast iron","carbon steel","dutch oven")

# meal / role
_m("breakfast","breakfast","brunch","pancakes","pancake","waffles","biscuits","scones","hash browns","hash","cinnamon rolls")
_m("lunch","lunch","quick lunch","lunch box")
_m("dinner","dinner","weeknight dinner","quick dinner")
_m("side","side","side dish")
_m("soup","soup","chowder","broth","broth-based","soup base","congee","juk","porridge","rice porridge")
_m("salad","salad","salads","noodle salads")
_m("noodles","noodles","noodle","rice noodles","chow mein","chow fun","lo mein","cold noodles","ramen","hand-pulled")
_m("rice","rice","fried rice","rice bowl","jasmine rice","glutinous rice","rice cakes")
_m("pasta","pasta")
_m("dessert","dessert","cookies","caramel","chocolate")
_m("snack","snack","appetizer","dim sum","street food")
_m("sandwich","sandwich","flatbread","naan","bread","buns","dumplings")
_m("dip","dip","dipping sauce","salsa")

# effort / context
_m("quick","quick","quick meal","quick cooking","quick-cooking","30-minute meals","15-minute","under 10 minutes","quick weeknight","quick weeknight dinner","easy","fast")
_m("weeknight","weeknight")
_m("make-ahead","make-ahead","make ahead","makes ahead","make-ahead friendly","advance preparation")
_m("batch-cook","batch cooking","meal prep")
_m("one-pan","one-pan","one-pot","one-pot meal","skillet-to-oven")
_m("budget","budget-friendly","thrifty")
_m("comfort-food","comfort food")
_m("healthy","healthy")

# flavor
_m("spicy","spicy","chili","chile","chiles","chile paste","chili oil","chili crisp","chile oil","hot and sour","hot peppers")
_m("umami","umami")

# technique-type facet (only meaningful on type=technique)
_m("knife-skills","knife skills","knife-skills","cutting","cutting technique","cutting techniques","slicing","dicing","julienne","mince","butchery","butchering","butcher cuts","filleting","knife technique","knife sharpening")
_m("prep","brining","dry-brining","dry-brine","dry brine","marinating","marinade","marinated","velveting","curing","dry cure","wet cure","salting","tenderizing","purging","resting","cold working")
_m("sauce-making","emulsion","emulsified","emulsification","emulsifying","reduction","pan sauce","pan-sauce","roux","béchamel","hollandaise","vinaigrette","glaze","glazing","thickening","monter au beurre","aioli","mayonnaise")
_m("foundational","stock","dashi","aromatics","maillard reaction","food science","cooking science","chemistry","food-lab","fundamental technique","foundational technique","clarified butter","ghee")

def normalize_tags(raw_tags: list[str]) -> list[str]:
    """Map a recipe's raw tag list to the clean controlled vocabulary.
    Deduplicates, preserves nothing outside VOCAB, drops noise silently."""
    out = set()
    for raw in raw_tags or []:
        key = raw.lower().strip()
        if key in DROP:
            continue
        if key in ALIAS:
            out.update(ALIAS[key])
        elif key in VOCAB:
            out.add(key)
        # else: silently dropped (ingredient names, one-off descriptors, etc.)
    return sorted(out)
