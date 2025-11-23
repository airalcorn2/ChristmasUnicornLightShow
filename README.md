# Christmas Unicorn Light Show

This repository contains the pieces for my (small) xLights show set to "Christmas Unicorn" by Sufjan Stevens.
This was my first time making a light show, so this repository is also meant to serve as a reminder of what I did.

## Purchasing a Controller

I used the Genius 16 version of the Commercial 16-Port Pixel Controller found [here](https://magicallightshows.com/products/commercial-quality-16-port-pixel-controller).
Note that this controller is massive overkill for this specific light show.
If you have the time and skills, you could build your own controller for much cheaper by picking up the various pieces (e.g., the Genius PRO: 8 Port Receiver [here](https://experiencelights.com/genius-pro-8-port-receiver/)) and assembling them together.

## Purchasing Pixel Lights

I used five strands of 50 12V Magicolour Pixels found [here](https://magicallightshows.com/products/pre-order-magicolour-pixels) (which all appear to be sold out now), i.e., I used 250 pixels.
There are other pixel manufacturers.
The main thing you're looking for is individually addressable RGB LEDs using the WS2811 control chip or one that's compatible (e.g., the Magiclour Pixels use a SKC6809 chip).

## Purchasing a Corrugated Plastic Sheet

I bought the Coroplast 36 in. x 72 in. x 0.157 in. (4mm) White Corrugated Twinwall Plastic Sheet seen [here](https://www.homedepot.com/p/Coroplast-36-in-x-72-in-x-0-157-in-4mm-White-Corrugated-Twinwall-Plastic-Sheet-COR-3672/202771364) from The Home Depot.
I used a power drill to drill holes into the sheet for the lights (I don't remember the size of the drill bit, unfortunately).

## Installing xLights

Follow the instructions on the xLights website [here](https://xlights.org/releases/).
I used the 2025.11 release on Fedora 43.
On Fedora 40, I ran into the issue described [here](https://github.com/xLightsSequencer/xLights/issues/4762), which was fixed by upgrading the `mesa-*` drivers as descrbed in the comment [here](https://github.com/xLightsSequencer/xLights/issues/4762#issuecomment-2669323812).

## Configuring the Controller

1. Connect the controller to your computer with an Ethernet cable.
My laptop does not have an Ethernet port, but using an Ethernet to USB adapter worked fine.
2. Plug in the power cord for the controller.
The controller will turn on.
Inside, there's a tiny screen that cycles through a few differnet displays.
One of them contains the static IP address for the controller (e.g., `192.168.1.50`).
Note this.
3. Assign the Ethernet connection on your computer a static IP address compatible with the controller.
This is probably the most complicated step not involving xLights.
[ChatGPT was helpful for me](https://chatgpt.com/share/6920dacd-a36c-8013-b250-cced819ef631).
	1. First, find your network interface:
    ```bash
    ifconfig
    ```
    Look for an interface such as `eth0` (for direct Ethernet connection) or `enp0s20f0u1c2` (if using a USB adapter).
    2. Set the IP address.
    To communicate with the controller, your computer must be on the same subnet (e.g., `192.168.1.xxx` with subnet mask `255.255.255.0`)
    ```bash
    sudo ip addr add 192.168.1.100/24 dev enp0s20f0u1c2
    ```
    The `/24` is CIDR notation (Classless Inter-Domain Routing) and specifies the subnet mask.
    3. Check that you can access the controller's web UI at its IP address (i.e., http://192.168.1.50 in this example) in your browser.
    If so, you're connected!
4. Plug in your lights (you might need to use multiple ports).
5. Test the lights.
    1. At the controller's web UI, click the "Testing" tab.

## Designing the Model

I used the web app [here](https://www.lightshowhub.com/tools/xlights-custom-model-builder) to design the model.
The creator of the app has a walkthrough video [here](https://www.youtube.com/watch?v=raI4KYlPdjU).
The model is based on the clip art [here](https://www.alamy.com/cute-clipart-christmas-unicorn-head-illustration-in-cartoon-style-cartoon-clip-art-christmas-unicorn-face-vector-illustration-of-an-animal-for-image472397693.html).

## Designing the Sequence

There's way more than can be covered here.
The xLights user manual can be found [here](https://manual.xlights.org/xlights).
The most important section for controlling "characters" is in the ["Singing Faces" section](https://manual.xlights.org/xlights/chapters/chapter-four-sequencer/singing-faces).

## Running the Sequence in xLights

1. In the **Sequencer** tab, click the **Render All** button (a painter's palette icon).
2. In the sequence, right click `Christmas_Unicorn [2]` and go to **Model > Play**.
If you see the preview of the show in the **Model Preview** pane, everything is working correctly.

## Running the Sequence on Your Lights

1. Open xLights.
2. Go the **Controller** tab.
3. Click the **Discover** button.
4. Make sure the IP address for the controller is correct.
5. In the top toolbar, go to **Tools > Test**.
6. Check the box for all your channels below **Select channels...**.
7. Select one of the tests (e.g., **Mixed Colors**). The pixels should be lighting up.
8. Open your sequence.
9. Click the **Output to Lights** button (a light bulb).
10. Play the sequence.

## Recording the Light Show

1. I recorded the video on my Pixel 10 Pro.
2. Before starting to record the video, I tapped and held on one of the lights to lock the focus.
3. After starting the recording, I started the light show, having my wife clap at the same time to serve as a cue for syncing the audio later.

## Editing the Video

1. I used Kdenlive to edit the final video.
2. All the project consisted of was a video track with the video, an audio track with the video's original audio, and an audio track for the MP3.
3. After lining up the MP3 audio track with the video using the clap cue, I removed the video's original audio track.
4. To export the video, in the top toolbar, go to **Project > Render**.
