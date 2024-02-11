# Frigate Plate Recognizer

Identify license plates via [Plate Recognizer](https://guides.platerecognizer.com/) or [CodeProject.AI](https://www.codeproject.com/) and add them as sublabels to [blakeblackshear/frigate](https://github.com/blakeblackshear/frigate)

### Setup

Create a `config.yml` file in your docker volume with the following contents:

```yml
frigate:
  frigate_url: http://127.0.0.1:5000
  mqtt_server: 127.0.0.1
  mqtt_username: username
  mqtt_password: password
  main_topic: frigate
  return_topic: plate_recognizer
  frigate_plus: false
  camera:
    - driveway_camera
  objects:
    - car
  min_score: .8
plate_recognizer:
  token: xxxxxxxxxx
  regions: 
    - us-ca
logger_level: INFO
```

Update your frigate url, mqtt server settings. If you are using mqtt authentication, update the username and password. Update the camera name(s) to match the camera name in your frigate config. Add your Plate Recognizer API key and region(s).

You'll need to make an account (free) [here](https://app.platerecognizer.com/accounts) and get an API key. You get up to 2,500 lookups per month for free. You will also need to enable car object detection for the cameras you want to use this with. See [here](https://guides.platerecognizer.com/docs/snapshot/getting-started/) on how to locally host Plate Recognizer.

You can specify a custom url for the plate_recognizer api by adding `api_url` to your config:

```yml
plate_recognizer:
  api_url: http://HOST-IP:8080/v1/plate-reader
  token: xxxxxxxxxx
  regions: 
    - us-ca
```

You can also filter by zones and/or cameras. If you want to filter by zones, add `zones` to your config:

```yml
frigate:
  # ...
  zones:
    - front_door
    - back_door
```

If no objects are speficied in the Frigate options, it will default to `[motorcycle, car, bus]`.

If you have a custom model with Frigate+ then it's able to detect license plates via an event's attributes, you can set `frigate_plus` to `true` in your config to activate this feature:

```yaml
frigate:
  # ... 
  frigate_plus: true
  license_plate_min_score: 0 # default is show all but can speficify a min score from 0 - 1 for example 0.8
  max_attempts: 20 # Optional: if set, will limit the number of snapshots sent for recognition for any particular event. 
```

If you're using CodeProject.AI, you'll need to comment out plate_recognizer in your config. Then add and update "api_url" with your CodeProject.AI Service API URL. Your config should look like:

```yml
#plate_recognizer:
#  token: xxxxxxxxxx
#  regions: 
#    - us-ca
code_project:
  api_url: http://127.0.0.1:32168/v1/image/alpr
```

### Running

```bash
docker run -v /path/to/config:/config -e TZ=America/New_York -it --rm --name frigate_plate_recognizer lmerza/frigate_plate_recognizer:latest
```

or using docker-compose:

```yml
services:
  frigate_plate_recognizer:
    image: lmerza/frigate_plate_recognizer:latest
    container_name: frigate_plate_recognizer
    volumes:
      - /path/to/config:/config
    restart: unless-stopped
    environment:
      - TZ=America/New_York
```

https://hub.docker.com/r/lmerza/frigate_plate_recognizer

### Debugging

set `logger_level` in your config to `DEBUG` to see more logging information:

```yml
logger_level: DEBUG
```

Logs will be in `/config/frigate_plate_recognizer.log`

### Save Snapshot Images to Path

If you want frigate-plate-recognizer to automatically save snapshots of recognized plates, add the following to your config.yml:

```yml
frigate:
  save_snapshots: True # Saves a snapshot called [Camera Name]_[timestamp].png
  draw_box: True # Optional - Draws a box around the plate on the snapshot along with the license plate text (Required Frigate plus setting)
  always_save_snapshot: True # Optional - will save a snapshot of every event sent to frigate_plate_recognizer, even if no plate is detected
```

Snapshots will be saved into the '/plates' directory within your container - to access them directly, map an additional volume within your docker-compose, e.g.:

```yml
services:
  frigate_plate_recognizer:
    image: lmerza/frigate_plate_recognizer:latest
    container_name: frigate_plate_recognizer
    volumes:
      - /path/to/config:/config
      - /path/to/plates:/plates:rw
    restart: unless-stopped
    environment:
      - TZ=America/New_York
```

### Monitor Watched Plates

If you want frigate-plate-recognizer to check recognized plates against a list of watched plates for close matches (including fuzzy recognition), add the following to your config.yml:

```yml
frigate:
  watched_plates: #list of plates to watch.
    -  ABC123
    -  DEF456
  fuzzy_match: 0.8 # default is test against plate-recognizer / CP.AI 'candidates' only, but can specify a min score for fuzzy matching if no candidates match watched plates from 0 - 1 for example 0.8
```

If a watched plate is found in the list of candidates plates returned by plate-recognizer / CP.AI, the response will be updated to use that plate and it's score. The original plate will be added to the MQTT response as an additional `original_plate` field.

If no candidates match and fuzzy_match is enabled with a value, the recognized plate is compared against each of the watched_plates using fuzzy matching. If a plate is found with a score > fuzzy_match, the response will be updated with that plate. The original plate and the associated fuzzy_score will be added to the MQTT response as additional fields `original_plate` and `fuzzy_score`.