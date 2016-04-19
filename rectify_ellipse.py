import numpy as np
import cv2
import sys
import ellipse
import scipy.optimize
import matplotlib.pyplot as plt

def translation(x, y):
    return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], dtype=float)

def rotation(theta):
    c = np.cos(theta)
    s = np.sin(theta)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)

def perspective_warp(a, b):
    return np.array([[1, 0, 0], [0, 1, 0], [a, b, 1]], dtype=float)

def slant(sx):
    return np.array([[1, sx, 0], [0, 1, 0], [0, 0, 1]], dtype=float)

def skewed_widths(contours, H):
    xvals = []
    for c in contours:
        pts = cv2.perspectiveTransform(c, H)
        x = pts[:,:,0]
        xvals.append( x.max() - x.min() )
    xvals = np.array(xvals)
    return np.sum(xvals)

def centered_warp(u0, v0, a, b):
    return np.dot(translation(u0, v0),
                  np.dot(perspective_warp(a, b),
                         translation(-u0, -v0)))

def warp_containing_points(img, pts, H, border=4):
    pts2 = cv2.perspectiveTransform(pts, H)
    x0, y0, w, h = cv2.boundingRect(pts2)
    T = translation(-x0+border, -y0+border)
    TH = np.dot(T, H)
    dst = cv2.warpPerspective(img, TH, (w+2*border, h+2*border),
                              borderMode=cv2.BORDER_REPLICATE)
    return dst, TH 

def conic_area_discrepancy(conics, H):

    areas = []
    
    for conic in conics:
        cx = ellipse.conic_transform(conic, H)
        k, ab = ellipse.conic_scale(cx)
        if np.isinf(ab):
            return np.inf
        areas.append(ab)

    areas = np.array(areas)
    areas /= areas.mean()
    areas -= 1 # subtract off mean
    
    return 0.5*np.dot(areas, areas)

def threshold(img):
    
    if len(img.shape) > 2:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        
    return cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY_INV, 101, 21)

def get_contours(img):

    work = threshold(img)

    if cv2.__version__[0] == '3':
        _, contours, _ = cv2.findContours(work, cv2.RETR_EXTERNAL,
                                          cv2.CHAIN_APPROX_SIMPLE)
    else:
        contours = cv2.findContours(work, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)[0]

    return contours

def get_conics(img, contours, abs_area_cutoff=10.0, mean_area_cutoff=0.15):

    conics = []
    used_contours = []
    areas = []
    pts = np.empty((0,1,2), dtype='float32')
    centroid = np.zeros(2)
    total_area = 0.0

    for c in contours:
        m = ellipse.moments_from_dict(cv2.moments(c))
        if m[0] > abs_area_cutoff:
            centroid += m[1:3]
            total_area += m[0]
            pts = np.vstack((pts, c.astype('float32')))
            conic = ellipse.conic_from_moments(m)
            conics.append(conic)
            areas.append(m[0])

    areas = np.array(areas)
    amean = areas.mean()

    print 'got', len(areas), 'contours with',  (areas < mean_area_cutoff*amean).sum(), 'small.'

    idx = np.where(areas > mean_area_cutoff*amean)[0]

    conics = np.array(conics)
    conics = conics[idx]
    centroid /= total_area

    display = img.copy()
    for conic in conics:
        x0, y0, a, b, theta = tuple(ellipse.params_from_conic(conic))
        cv2.ellipse(display, (int(x0), int(y0)), (int(a), int(b)),
                    theta*180/np.pi, 0, 360, (0,0,255))

    cv2.imwrite('debug0_conics.png', display)

    contours = [contours[i].astype('float32') for i in idx]

    return conics, contours, centroid

def optimize_conics(conics, p0):

    x0 = np.array([0.0, 0.0])
    
    hfunc = lambda x: centered_warp(p0[0], p0[1], x[0], x[1])
    
    f = lambda x: conic_area_discrepancy(conics, hfunc(x))
    
    res = scipy.optimize.minimize(f, x0, method='Powell')

    H = hfunc(res.x)

    return H

def orientation_detect(img, contours, H, rho=8.0, ntheta=256):

    pts = np.vstack(tuple(contours))
    warped, _ = warp_containing_points(img, pts, H)
    
    work = threshold(warped)
    work = cv2.Canny(work, 10, 100)

    thetas = np.linspace(-0.5*np.pi, 0.5*np.pi, ntheta+1)[:-1]
    irho = 1.0/rho

    h, w = work.shape
    rmax = int(np.ceil(np.hypot(w, h)*irho))

    hist = np.zeros((rmax, ntheta))
    v, u = np.mgrid[0:h, 0:w]
    v = v[work.view(bool)]
    u = u[work.view(bool)]

    u0 = w*0.5
    v0 = h*0.5

    for i, theta in enumerate(thetas):
        r = (-(u-u0)*np.sin(theta) + (v-v0)*np.cos(theta))*irho + 0.5*rmax
        assert( r.min() > 0 and r.max() < rmax )
        bc = np.bincount((r + 0.5).astype(int))
        hist[:len(bc),i] = bc

    stats = np.zeros((2, ntheta))

    for i in range(ntheta):
        stats[0,i] = (hist[:,i] == 0).sum() # number of nonzero pixels in hough histogram
        stats[1,i] = hist[:,i].sum() # total sum of histogram

    # first key is total # zeros, second key is column sum 
    idx = np.lexsort( (-stats[0,:], stats[1,:])  )

    debug_hist = (255*hist/hist.max()).astype('uint8')
    debug_hist = cv2.cvtColor(debug_hist, cv2.COLOR_GRAY2RGB)
    cv2.line(debug_hist, (idx[0],0), (idx[0],rmax), (255,0,0))
    cv2.imwrite('debug1_histogram.png', debug_hist)
    
    theta = thetas[idx[0]]

    p0 = np.array((u0, v0))
    t = np.array((np.cos(theta), np.sin(theta)))

    cv2.line(warped,
             tuple(map(int, p0 - rho*rmax*t)),
             tuple(map(int, p0 + rho*rmax*t)),
             (255, 0, 0))

    cv2.imwrite('debug2_prerotate.png', warped)
                 
    RH = np.dot(rotation(-theta), H)
    
    warped, _ = warp_containing_points(img, pts, RH)
    cv2.imwrite('debug3_preskew.png', warped)
    
    return RH


def skew_detect(img, contours, RH):

    pts = np.vstack(tuple([cv2.convexHull(c) for c in contours]))

    f = lambda x: skewed_widths(contours, np.dot(slant(x), RH))

    res = scipy.optimize.minimize_scalar(f, (-2.0, 0.0, 2.0))

    SRH = np.dot(slant(res.x), RH)
    warped, Hfinal = warp_containing_points(img, pts, SRH)

    cv2.imwrite('debug4_final.png', warped)

    return SRH
    
img = cv2.imread(sys.argv[1])
contours = get_contours(img)

conics, contours, centroid = get_conics(img, contours)
H = optimize_conics(conics, centroid)
RH = orientation_detect(img, contours, H)
SRH = skew_detect(img, contours, RH)



