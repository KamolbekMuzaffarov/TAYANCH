from __future__ import annotations

# geoBoundaries name -> Uzbek (Latin) display name. Unknown names are kept as they are.
DISTRICT_UZ: dict[str, str] = {
    # Andijon
    "Shirin city": "Shirin shahri", "Andijan": "Andijon tumani", "Ulugnar": "Ulugʻnor", "Khadjaabad": "Xoʻjaobod",
    "Markhamat": "Marhamat", "Asaka": "Asaka", "Shakhrixan": "Shahrixon", "Boz": "Boʻz", "Djalalkuduk": "Jalaquduq",
    "Bulakbashi": "Buloqboshi", "Kurgantepa": "Qoʻrgʻontepa", "Balikchi": "Baliqchi", "Khanabad city": "Xonobod shahri",
    "Altinkul": "Oltinkoʻl", "Andijan city": "Andijon shahri", "Izboskan": "Izboskan", "Paxtaabad": "Paxtaobod",
    # Buxoro
    "Jondor": "Jondor", "Bukhara": "Buxoro tumani", "Alat": "Olot", "Karakul": "Qorakoʻl", "Karaulbazar": "Qorovulbozor",
    "Shafirkan": "Shofirkon", "Ramitan": "Romitan", "Peshku": "Peshkoʻ", "Kagan": "Kogon tumani", "Kagan city": "Kogon shahri",
    "Bukhara city": "Buxoro shahri", "Gijduvan": "Gʻijduvon", "Vabkent": "Vobkent",
    # Jizzax
    "Farish": "Forish", "Gallyaaral": "Gʻallaorol", "Yangiabad": "Yangiobod", "Dzhizak city": "Jizzax shahri",
    "Bakhmal": "Baxmal", "Bulungur": "Bulungʻur", "Sharof Rashidov": "Sharof Rashidov", "Zafarabad": "Zafarobod",
    "Zarbdar": "Zarbdor", "Zaamin": "Zomin", "Paxtakor": "Paxtakor", "Dustlik": "Doʻstlik", "Mirzachul": "Mirzachoʻl",
    "Arnasay": "Arnasoy",
    # Qashqadaryo
    "Kasbi": "Kasbi", "Mirishkar": "Mirishkor", "Nishan": "Nishon", "Mubarek": "Muborak", "Kitab": "Kitob",
    "Kamashi": "Qamashi", "Karshi": "Qarshi tumani", "Guzar": "Gʻuzor", "Dehkanabad": "Dehqonobod", "Kasan": "Koson",
    "Karshi city": "Qarshi shahri", "Chirakchi": "Chiroqchi", "Yakkabag": "Yakkabogʻ", "Shakhrisabz": "Shahrisabz tumani",
    "Shakhrisabz city": "Shahrisabz shahri",
    # Navoiy
    "Nurata": "Nurota", "Navoi city": "Navoiy shahri", "Kiziltepa": "Qiziltepa", "Karmana": "Karmana",
    "Navbakhor": "Navbahor", "Khatirchi": "Xatirchi", "Uchkuduk": "Uchquduq", "Kanimekh": "Konimex", "Tamdi": "Tomdi",
    "Zarafshan city": "Zarafshon shahri",
    # Namangan
    "Pap": "Pop", "Mingbulak": "Mingbuloq", "Narin": "Norin", "Chust": "Chust", "Namangan": "Namangan tumani",
    "Turakurgan": "Toʻraqoʻrgʻon", "Yangikurgan": "Yangiqoʻrgʻon", "Kasansay": "Kosonsoy", "Namangan city": "Namangan shahri",
    "Uychi": "Uychi", "Chartak": "Chortoq", "Uchkurgan": "Uchqoʻrgʻon",
    # Samarqand
    "Nurabad": "Nurobod", "Pakhtachi": "Paxtachi", "Pastdargom": "Pastdargʻom", "Ishtikhan": "Ishtixon", "Narpay": "Narpay",
    "Kattakurgan": "Kattaqoʻrgʻon tumani", "Kattakurgan city": "Kattaqoʻrgʻon shahri", "Koshrabad": "Qoʻshrabot",
    "Samarkand city": "Samarqand shahri", "Urgut": "Urgut", "Samarkand": "Samarqand tumani", "Taylak": "Toyloq",
    "Dzhambay": "Jomboy", "Payarik": "Payariq", "Akdarya": "Oqdaryo",
    # Surxondaryo
    "Termez": "Termiz tumani", "Baysun": "Boysun", "Muzrabad": "Muzrabot", "Sherabad": "Sherobod", "Angor": "Angor",
    "Sariasiya": "Sariosiyo", "Dzharkurgan": "Jarqoʻrgʻon", "Termez city": "Termiz shahri", "Kizirik": "Qiziriq",
    "Shurchi": "Shoʻrchi", "Kumkurgan": "Qumqoʻrgʻon", "Uzun": "Uzun", "Altinsay": "Oltinsoy", "Denau": "Denov",
    # Sirdaryo
    "Sardoba": "Sardoba", "Khavas": "Xovos", "Mirzaabad": "Mirzaobod", "Bayaut": "Boyovut", "Yangiyer city": "Yangiyer shahri",
    "Gulistan city": "Guliston shahri", "Saykhunabad": "Sayxunobod", "Gulistan": "Guliston tumani", "Akaltin": "Oqoltin",
    "Sirdarya": "Sirdaryo",
    # Toshkent shahri
    "Yakkasaray": "Yakkasaroy", "Almazar": "Olmazor", "Shaykhantokhur": "Shayxontohur", "Yunusabad": "Yunusobod",
    "Mirzo Ulugbek": "Mirzo Ulugʻbek",
    # Toshkent viloyati (geoBoundaries lists several city districts here)
    "Bekabad": "Bekobod tumani", "Bekabad city": "Bekobod shahri", "Urtachirchik": "Oʻrtachirchiq",
    "Kuyichirchik": "Quyichirchiq", "Chinaz": "Chinoz", "Akhangaran": "Ohangaron tumani", "Buka": "Boʻka",
    "Akkurgan": "Oqqoʻrgʻon", "Pskent": "Piskent", "Almalik city": "Olmaliq shahri", "Akhangaran city": "Ohangaron shahri",
    "Bektemir": "Bektemir", "Sergeli": "Sergeli", "Yangiyul": "Yangiyoʻl tumani", "Yangiyul city": "Yangiyoʻl shahri",
    "Zangiata": "Zangiota", "Chilanzar": "Chilonzor", "Uchtepa": "Uchtepa", "Tashkent": "Toshkent tumani",
    "Yukarichirchik": "Yuqorichirchiq", "Parkent": "Parkent", "Angren city": "Angren shahri", "Kibray": "Qibray",
    "Yashnobod": "Yashnobod", "Mirabad": "Mirobod", "Bostanlik": "Boʻstonliq", "Chirchik city": "Chirchiq shahri",
    "Nurafshon city": "Nurafshon shahri",
    # Fargʻona
    "Bagdad": "Bagʻdod", "Uzbekistan": "Oʻzbekiston", "Besharik": "Beshariq", "Sokh": "Soʻx", "Fergana": "Fargʻona tumani",
    "Rishtan": "Rishton", "Altiarik": "Oltiariq", "Uchkuprik": "Uchkoʻprik", "Kuvasay city": "Quvasoy shahri",
    "Fergana city": "Fargʻona shahri", "Kokand city": "Qoʻqon shahri", "Furkat": "Furqat", "Kushtepa": "Qoʻshtepa",
    "Buvayda": "Buvayda", "Dangara": "Dangʻara", "Yazyavan": "Yozyovon", "Kuva": "Quva", "Tashlak": "Toshloq",
    "Margilan city": "Margʻilon shahri",
    # Xorazm
    "Khiva": "Xiva tumani", "Shavat": "Shovot", "Koshkupir": "Qoʻshkoʻpir", "Gurlen": "Gurlan", "Khazarasp": "Xazorasp",
    "Khanka": "Xonqa", "Yangiarik": "Yangiariq", "Bagat": "Bogʻot", "Urgench": "Urganch tumani", "Yangibazar": "Yangibozor",
    "Urgench city": "Urganch shahri", "Khiva city": "Xiva shahri",
    # Qoraqalpogʻiston
    "Karauzyak": "Qoraoʻzak", "Kungrad": "Qoʻngʻirot", "Amudarya": "Amudaryo", "Chimbay": "Chimboy", "Kanlikul": "Qanlikoʻl",
    "Shumanay": "Shumanay", "Khojeyli": "Xoʻjayli", "Kegeyli": "Kegeyli", "Muynak": "Moʻynoq", "Nukus": "Nukus tumani",
    "Takhtakupir": "Taxtakoʻpir", "Nukus city": "Nukus shahri", "Turtkul": "Toʻrtkoʻl", "Ellikkala": "Ellikqalʼa",
    "Beruniy": "Beruniy",
}


def district_uz(name: str) -> str:
    key = name.replace("а", "a").replace("о", "o").replace("е", "e").strip()  # stray Cyrillic look-alikes in the source
    return DISTRICT_UZ.get(key, name)


FIRST_NAMES = [
    "Madina", "Zilola", "Shahnoza", "Nigora", "Gulnora", "Feruza", "Dildora", "Mohira", "Sevara", "Kamola", "Oysha",
    "Barno", "Malika", "Nargiza", "Dilnoza", "Gulchehra", "Mavluda", "Nodira", "Zarina", "Shahlo", "Umida", "Nilufar",
    "Ozoda", "Sabohat", "Mahliyo", "Durdona", "Iroda", "Lola", "Munisa", "Robiya", "Sarvinoz", "Gulshan", "Dilorom",
    "Mohinur", "Zuhra", "Fotima", "Hulkar", "Charos", "Maftuna", "Shoira", "Yulduz", "Nasiba", "Oydin", "Rayhona",
    "Saida", "Xurshida", "Zebo", "Aziza", "Bahora", "Diyora", "Gavhar", "Hilola", "Komila", "Laylo", "Mehriniso",
    "Nozima", "Parizoda", "Sitora", "Tursunoy", "Vazira", "Zulfiya", "Muazzam", "Ruxshona", "Shahzoda", "Odina",
    "Mubina", "Soliha", "Asal", "Gulsanam", "Dilrabo",
]
LAST_NAMES = [
    "Tursunova", "Rahimova", "Abdullayeva", "Ismoilova", "Saidova", "Yoʻldosheva", "Nazarova", "Qodirova", "Aliyeva",
    "Ergasheva", "Mirzayeva", "Xolmatova", "Karimova", "Yusupova", "Toshmatova", "Usmonova", "Rustamova", "Sharipova",
    "Normatova", "Hamidova", "Joʻrayeva", "Boboyeva", "Olimova", "Sobirova", "Xasanova", "Husanova", "Mahmudova",
    "Ahmedova", "Salimova", "Azimova", "Gʻaniyeva", "Zokirova", "Nematova", "Otajonova", "Qosimova", "Raxmonova",
    "Sultonova", "Toʻxtayeva", "Umarova", "Valiyeva", "Xudoyberdiyeva", "Eshonqulova", "Murodova", "Nurmatova",
    "Oripova", "Poʻlatova", "Safarova", "Tojiboyeva", "Shodiyeva", "Haydarova", "Ibragimova", "Jalilova", "Latipova",
    "Mamajonova", "Niyozova", "Roʻziyeva",
]
DOCTOR_FIRST = ["Nodira", "Gulnoza", "Dilbar", "Mahbuba", "Saodat", "Nargiza", "Zulayho", "Feruza", "Lobar", "Shahnoza",
                "Munira", "Dilfuza", "Rano", "Malohat", "Barchinoy", "Guzal"]
MAHALLAS = [
    "Bogʻishamol", "Guliston", "Navroʻz", "Istiqlol", "Doʻstlik", "Tinchlik", "Yangiobod", "Bunyodkor", "Mustaqillik",
    "Oq oltin", "Navbahor", "Gulzor", "Obod", "Yoshlik", "Zarafshon", "Paxtakor", "Olmazor", "Chashma", "Sohibqiron",
    "Nurafshon", "Bogʻbon", "Hamkor", "Ibn Sino", "Beruniy", "Ulugʻbek", "Shodlik", "Mehnatobod", "Qoʻrgʻoncha",
    "Tepaqoʻrgʻon", "Humo", "Oqtepa", "Yangi hayot", "Birlik", "Farovon", "Namuna", "Saxovat",
]
HISTORY_CODES = ["previous_cesarean", "previous_preeclampsia", "previous_gestational_hypertension", "chronic_hypertension",
                 "diabetes", "anaemia_history", "short_birth_interval", "previous_preterm_birth", "thyroid_disease",
                 "kidney_disease", "previous_stillbirth"]
