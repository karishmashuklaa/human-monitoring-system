from lib.centroidtracker import CentroidTracker
from lib.trackableobject import TrackableObject
from lib import config, threading
from lib.mailer import Mailer
from imutils.video import VideoStream
from imutils.video import FPS
import argparse, imutils
import time, schedule, csv
import time, dlib, cv2, datetime
from itertools import zip_longest
import numpy as np

t0 = time.time()

def run():
    ap = argparse.ArgumentParser()
    ap.add_argument('-p', 'prototxt', required=True, 
    help='Path to Caffe deploy prototxt file')
    ap.add_argument('-m', '--model', required=True, 
    help='Path to Caffe pre-trained model')
    ap.add_argument('-i', '--input', type=str, 
    help='Path to optional input video file')
    ap.add_argument('-o', '--ouput', type=str, 
    help='Path to optional output video file')
    ap.add_argument('-c', '--confidence', type=float, default=0.4, 
    help='Minimum probability to filter weak detections')
    ap.add_argument('-s', '--skip-frames', type=int, default=30, 
    help='# of skip frames between detections')
    args = vars(ap.parse_args())

    # classes the model was trained to detect
    CLASSES = ['background', 'aeroplane', 'bicycle', 'bird', 'boat', 
    'bottle', 'bus', 'car', 'cat', 'chair', 'cow', 'diningtable', 
    'dog', 'horse', 'motorbike', 'person', 'pottedplant', 
    'sheep','sofa', 'train', 'tvmonitor']

    # load our serialized model from disk
    print('[INFO] loading model...')
    net = cv2.dnn.readNetFromCaffe(args['prototxt'], args['model'])

    # if video path is not supplied, use IP/Webcam
    if not args.get('input', False):
        print('[INFO] Starting the live stream...')
        vs = VideoStream(config.url).start()
        time.sleep(2.0)
    else:
        print('[INFO] Starting the video...')
        vs = cv2.VideoCapture(args['input'])
    
    # initialise the video writer
    writer = None

    # initialize the frame dimensions (we'll set them as soon as we read
	# the first frame from the video)
    W = None
    H = None 

    # instantiate our centroid tracker, then initialise a list 
    # to store each of our dlib correlation trackers, and a dict to
    # map each unique object ID to a TrackableObject
    ct = CentroidTracker(maxDisappeared=40, maxDistance=50)
    trackers = []
    trackableObjects = {}

    # initialise the total number of frames processed thus far, along with 
    # the total no. of objects that have moved either up or down
    totalFrames = 0
    totalDown = 0
    totalUp = 0
    x = []
    emptyUp = []
    emptyDown = []

    # start the frames per second throughput estimator
    fps = FPS().start()

    # loop over incoming frames from the video stream
    while True:
        # grab the next frame and handle if we are reading from
        # either VideoCapture or VideoStream
        frame = vs.read()
        frame = frame[1] if args.get('input', False) else frame

        # if we are viewing a video and did not grab a frame
        # then we have reached end of the video
        if args['input'] is not None and frame is None: 
            break

        # resize the frame to have a max width of 500px(the less 
        # data we have, the faster we can process it), then
        # convert the frame from BGR to RGB for dlib
        frame = imutils.resize(frame,width=500)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # if the frame dimensions are empty, set them
        if W is None or H is None:
            (H, W) = frame.shape[:2]

        # if we're supposed to be writing a video to disk, initialize the writer
        if args['output'] is not None and writer is None:
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            writer = cv2.VideoWriter(args['output'], fourcc, 30, (W,H), True)

        # initialize the current status along with our list of 
        # bounding box rects returned by either
        # 1. Our object detector or 2. Correlation trackers
        status = 'Waiting'
        rects = []

        # check to see if we should run a more computionally expensive
        # object detection method to aid our tracker
        if totalFrames % args['skip_frames'] == 0:
            # set the status and initialize our new set of object trackers
            status = 'Detecting'
            trackers = []

            # convert the frame to a blob and pass the blob through the 
            # network and obtain the detections
            blob = cv2.dnn.blobFromImage(frame, 0.007843, (W,H), 127.5)
            net.setInput(blob)
            detections = net.foward()

        # loop over detections
        for i in np.arange(0, detections.shape[2]):
            # extract the confidence (probability) associated with the prediction
            confidence = detections[0, 0, i, 2]

            # filter out weak detections by requiring a min confidence
            if confidence > args['confidence']:
                # extract the index of the class label from the detections list
                idx = int(detections[0, 0, i, 1])

                # if the class label is not a person, ignore it 
                if CLASSES[idx] != 'person':
                    continue

                # compute the (x,y) coordinates of the bounding box
                # for the object
                box = detections[0, 0, i, 3:7] * np.array([W, H, W, H])
                (startX, startY, endX, endY) = box.astype('int')

                # construct a dlib rectangle object from the bounding box coordinates
                # and then start the dlib correlation tracker
                tracker = dlib.correlation_tracker()
                rect = dlib.rectangle(startX, startY, endX, endY)
                tracker.start_track(rgb, rect)

                # add the tracker to our list of trackers so we can
                # utilize it during skip frames
                trackers.append(tracker)

        # otherwise, we should utilize our object *trackers* rather than
	    # object *detectors* to obtain a higher frame processing throughput
        else: 
            # loop over trackers
            for tracker in trackers:
                # set the status of our system to be tracking
                # rather than of waiting/detecting
                status = 'Tracking'

                # update the tracker and grab the updated position
                tracker.update(rgb)
                pos = tracker.get_position()

                # unpack the position object
                startX = int(pos.left())
                startY = int(pos.top())
                endX = int(pos.right())
                endY = int(pos.bottom())

                # add the bounding box coordinates to the rectangles list
                rects.append((startX, startY, endX, endY))

        # draw a horizontal line in the center of the frame -- once an
		# object crosses this line we will determine whether they were
		# moving 'up' or 'down'
		cv2.line(frame, (0, H // 2), (W, H // 2), (0, 0, 0), 3)
		cv2.putText(frame, "-Prediction border-Entrance-", (10, H - ((i * 20) + 200)),
			cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        
        # use the centroid tracker to associate the
        # 1. Old object centroids with
        # 2. Newly computed object centroids
        objects = ct.update(rects)

        # loop over tracked objects
        for (objectID, centroid) in objects.items():
            # check to see if a trackable object exists for the current object ID
            trackObj = trackableObjects.get(objectID, None)

            # if there is no existing trackable object, create one
            if trackObj is None:
                trackObj = TrackableObject(objectID, centroid)
            # otherwise there is a trackable object which can be used to determine direction
            else:
                # the difference between the y-coordinate of the *current*
				# centroid and the mean of *previous* centroids will tell
				# us in which direction the object is moving (negative for
				# 'up' and positive for 'down')
                y = [c[1] for c in trackObj.centroids]
                direction = centroid[1] - np.mean(y)
                trackObj.centroids.append(centroid)

                # check if the object has been counted or not
                if not trackObj.counted:
                    # if the direction is negative (indicating the object
                    # is moving UP) AND the centroid is above the center line,
                    # count the object
                    if direction < 0 and centroid[1] < H // 2:
                        totalUp += 1
                        emptyUp.append(totalUp)
                        trackObj.counted = True

                    # if the direction is positive(indicating the object
                    # is movng DOWN) AND the centroid is below the center line
                    # count the object
                    elif direction > 0 and centroid[1] > H // 2:
                        totalDown += 1
                        emptyDown.append(totalDown)
                        # if the people limit exceeds over threshold, send an email
                        if sum(x) >= config.Threshold:
                            cv2.putText(frame, 'ALERT-People Limit Exceeded!',(10, frame.shape[0] - 80), cv2.FONT_HERSHEY_COMPLEX, 0.5, (0, 0, 255), 2)
                            if config.ALERT:
                                print('[INFO] Sending an email alert...')
                                Mailer().send(config.MAIL)
                                print('[INFO] Alert sent successfully.')

                        trackObj.counted = True
                    x = []
                    # compute the sum of total people inside
                    x.append(len(emptyDown) - len(emptyUp))

            # store the trackable object in our dictionary
            trackableObjects[objectID] = trackObj