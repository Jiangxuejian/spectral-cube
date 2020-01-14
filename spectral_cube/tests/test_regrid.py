import sys
import pytest
import tempfile
import numpy as np

from astropy import units as u
from astropy import convolution
from astropy.wcs import WCS
from astropy import wcs
from astropy.io import fits

try:
    import tracemalloc
    tracemallocOK = True
except ImportError:
    tracemallocOK = False

# The comparison of Quantities in test_memory_usage
# fail with older versions of numpy
from distutils.version import LooseVersion

NPY_VERSION_CHECK = LooseVersion(np.version.version) >= "1.13"

from radio_beam import beam, Beam

from .. import SpectralCube
from ..utils import WCSCelestialError
from .test_spectral_cube import cube_and_raw
from .test_projection import load_projection
from . import path, utilities


def test_convolution(data_255_delta):
    cube, data = cube_and_raw(data_255_delta)

    # 1" convolved with 1.5" -> 1.8027....
    target_beam = Beam(1.802775637731995*u.arcsec, 1.802775637731995*u.arcsec,
                       0*u.deg)

    conv_cube = cube.convolve_to(target_beam)

    expected = convolution.Gaussian2DKernel((1.5*u.arcsec /
                                             beam.SIGMA_TO_FWHM /
                                             (5.555555555555e-4*u.deg)).decompose().value,
                                            x_size=5, y_size=5,
                                           )

    expected.normalize()

    np.testing.assert_almost_equal(expected.array,
                                   conv_cube.filled_data[0,:,:].value)

    # 2nd layer is all zeros
    assert np.all(conv_cube.filled_data[1,:,:] == 0.0)


def test_beams_convolution(data_455_delta_beams):
    cube, data = cube_and_raw(data_455_delta_beams)

    # 1" convolved with 1.5" -> 1.8027....
    target_beam = Beam(1.802775637731995*u.arcsec, 1.802775637731995*u.arcsec,
                       0*u.deg)

    conv_cube = cube.convolve_to(target_beam)

    pixscale = wcs.utils.proj_plane_pixel_area(cube.wcs.celestial)**0.5*u.deg

    for ii, bm in enumerate(cube.beams):
        expected = target_beam.deconvolve(bm).as_kernel(pixscale, x_size=5,
                                                        y_size=5)
        expected.normalize()

        np.testing.assert_almost_equal(expected.array,
                                       conv_cube.filled_data[ii,:,:].value)


def test_beams_convolution_equal(data_522_delta_beams):
    cube, data = cube_and_raw(data_522_delta_beams)

    # Only checking that the equal beam case is handled correctly.
    # Fake the beam in the first channel. Then ensure that the first channel
    # has NOT been convolved.
    target_beam = Beam(1.0 * u.arcsec, 1.0 * u.arcsec, 0.0 * u.deg)
    cube.beams.major[0] = target_beam.major
    cube.beams.minor[0] = target_beam.minor
    cube.beams.pa[0] = target_beam.pa

    conv_cube = cube.convolve_to(target_beam)

    np.testing.assert_almost_equal(cube.filled_data[0].value,
                                   conv_cube.filled_data[0].value)


@pytest.mark.parametrize('use_memmap', (True, False))
def test_reproject(use_memmap, data_adv):

    pytest.importorskip('reproject')

    cube, data = cube_and_raw(data_adv)

    wcs_in = WCS(cube.header)
    wcs_out = wcs_in.deepcopy()
    wcs_out.wcs.ctype = ['GLON-SIN', 'GLAT-SIN', wcs_in.wcs.ctype[2]]
    wcs_out.wcs.crval = [134.37608, -31.939241, wcs_in.wcs.crval[2]]
    wcs_out.wcs.crpix = [2., 2., wcs_in.wcs.crpix[2]]

    header_out = cube.header
    header_out['NAXIS1'] = 4
    header_out['NAXIS2'] = 5
    header_out['NAXIS3'] = cube.shape[0]
    header_out.update(wcs_out.to_header())

    result = cube.reproject(header_out, use_memmap=use_memmap)

    assert result.shape == (cube.shape[0], 5, 4)
    # Check WCS in reprojected matches wcs_out
    assert wcs_out.wcs.compare(result.wcs.wcs)
    # And that the headers have equivalent WCS info.
    result_wcs_from_header = WCS(result.header)
    assert result_wcs_from_header.wcs.compare(wcs_out.wcs)


def test_spectral_smooth(data_522_delta):

    cube, data = cube_and_raw(data_522_delta)

    result = cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0), use_memmap=False)

    np.testing.assert_almost_equal(result[:,0,0].value,
                                   convolution.Gaussian1DKernel(1.0,
                                                                x_size=5).array,
                                   4)

    result = cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0), use_memmap=True)

    np.testing.assert_almost_equal(result[:,0,0].value,
                                   convolution.Gaussian1DKernel(1.0,
                                                                x_size=5).array,
                                   4)

def test_catch_kernel_with_units(data_522_delta):
    # Passing a kernel with a unit should raise a u.UnitsError

    cube, data = cube_and_raw(data_522_delta)

    with pytest.raises(u.UnitsError,
                       match="The convolution kernel should be defined without a unit."):
        cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0 * u.one),
                             use_memmap=False)


def test_spectral_smooth_4cores(data_522_delta):

    pytest.importorskip('joblib')

    cube, data = cube_and_raw(data_522_delta)

    result = cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0), num_cores=4, use_memmap=True)

    np.testing.assert_almost_equal(result[:,0,0].value,
                                   convolution.Gaussian1DKernel(1.0,
                                                                x_size=5).array,
                                   4)

    # this is one way to test non-parallel mode
    result = cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0), num_cores=4, use_memmap=False)

    np.testing.assert_almost_equal(result[:,0,0].value,
                                   convolution.Gaussian1DKernel(1.0,
                                                                x_size=5).array,
                                   4)

    # num_cores = 4 is a contradiction with parallel=False, so we want to make
    # sure it fails
    with pytest.raises(ValueError) as exc:
        result = cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0),
                                      num_cores=4, parallel=False)
    assert exc.value.args[0] == ("parallel execution was not requested, but "
                                 "multiple cores were: these are incompatible "
                                 "options.  Either specify num_cores=1 or "
                                 "parallel=True")

    np.testing.assert_almost_equal(result[:,0,0].value,
                                   convolution.Gaussian1DKernel(1.0,
                                                                x_size=5).array,
                                   4)


def test_spectral_smooth_fail(data_522_delta_beams):

    cube, data = cube_and_raw(data_522_delta_beams)

    with pytest.raises(AttributeError,
                       match=("VaryingResolutionSpectralCubes can't be "
                              "spectrally smoothed.  Convolve to a "
                              "common resolution with `convolve_to` before "
                              "attempting spectral smoothed.")):
        cube.spectral_smooth(kernel=convolution.Gaussian1DKernel(1.0))


def test_spectral_interpolate(data_522_delta):

    cube, data = cube_and_raw(data_522_delta)

    orig_wcs = cube.wcs.deepcopy()

    # midpoint between each position
    sg = (cube.spectral_axis[1:] + cube.spectral_axis[:-1])/2.

    result = cube.spectral_interpolate(spectral_grid=sg)

    np.testing.assert_almost_equal(result[:,0,0].value,
                                   [0.0, 0.5, 0.5, 0.0])

    assert cube.wcs.wcs.compare(orig_wcs.wcs)


def test_spectral_interpolate_with_fillvalue(data_522_delta):

    cube, data = cube_and_raw(data_522_delta)

    # Step one channel out of bounds.
    sg = ((cube.spectral_axis[0]) -
          (cube.spectral_axis[1] - cube.spectral_axis[0]) *
          np.linspace(1,4,4))
    result = cube.spectral_interpolate(spectral_grid=sg,
                                       fill_value=42)
    np.testing.assert_almost_equal(result[:,0,0].value,
                                   np.ones(4)*42)


def test_spectral_interpolate_fail(data_522_delta_beams):

    cube, data = cube_and_raw(data_522_delta_beams)

    with pytest.raises(AttributeError,
                       match=("VaryingResolutionSpectralCubes can't be "
                              "spectrally interpolated.  Convolve to a "
                              "common resolution with `convolve_to` before "
                              "attempting spectral interpolation.")):
        cube.spectral_interpolate(5)


def test_spectral_interpolate_with_mask(data_522_delta):

    hdul = fits.open(data_522_delta)
    hdu = hdul[0]

    # Swap the velocity axis so indiff < 0 in spectral_interpolate
    hdu.header["CDELT3"] = - hdu.header["CDELT3"]

    cube = SpectralCube.read(hdu)

    mask = np.ones(cube.shape, dtype=bool)
    mask[:2] = False

    masked_cube = cube.with_mask(mask)

    orig_wcs = cube.wcs.deepcopy()

    # midpoint between each position
    sg = (cube.spectral_axis[1:] + cube.spectral_axis[:-1])/2.

    result = masked_cube.spectral_interpolate(spectral_grid=sg[::-1])

    # The output makes CDELT3 > 0 (reversed spectral axis) so the masked
    # portion are the final 2 channels.
    np.testing.assert_almost_equal(result[:, 0, 0].value,
                                   [0.0, 0.5, np.NaN, np.NaN])

    assert cube.wcs.wcs.compare(orig_wcs.wcs)

    hdul.close()


def test_spectral_interpolate_reversed(data_522_delta):

    cube, data = cube_and_raw(data_522_delta)

    orig_wcs = cube.wcs.deepcopy()

    # Reverse spectral axis
    sg = cube.spectral_axis[::-1]

    result = cube.spectral_interpolate(spectral_grid=sg)

    np.testing.assert_almost_equal(sg.value, result.spectral_axis.value)


def test_convolution_2D(data_55_delta):

    proj, hdu = load_projection(data_55_delta)

    # 1" convolved with 1.5" -> 1.8027....
    target_beam = Beam(1.802775637731995*u.arcsec, 1.802775637731995*u.arcsec,
                       0*u.deg)

    conv_proj = proj.convolve_to(target_beam)

    expected = convolution.Gaussian2DKernel((1.5*u.arcsec /
                                             beam.SIGMA_TO_FWHM /
                                             (5.555555555555e-4*u.deg)).decompose().value,
                                            x_size=5, y_size=5,
                                           )
    expected.normalize()

    np.testing.assert_almost_equal(expected.array,
                                   conv_proj.value)
    assert conv_proj.beam == target_beam



def test_nocelestial_convolution_2D_fail(data_255_delta):

    cube, data = cube_and_raw(data_255_delta)

    proj = cube.moment0(axis=1)

    test_beam = Beam(1.0 * u.arcsec)

    with pytest.raises(WCSCelestialError,
                       match="WCS does not contain two spatial axes."):
        proj.convolve_to(test_beam)


def test_reproject_2D(data_55):

    pytest.importorskip('reproject')

    proj, hdu = load_projection(data_55)

    wcs_in = WCS(proj.header)
    wcs_out = wcs_in.deepcopy()
    wcs_out.wcs.ctype = ['GLON-SIN', 'GLAT-SIN']
    wcs_out.wcs.crval = [134.37608, -31.939241]
    wcs_out.wcs.crpix = [2., 2.]

    header_out = proj.header
    header_out['NAXIS1'] = 4
    header_out['NAXIS2'] = 5
    header_out.update(wcs_out.to_header())

    result = proj.reproject(header_out)

    assert result.shape == (5, 4)
    assert result.beam == proj.beam

    # Check WCS in reprojected matches wcs_out
    assert wcs_out.wcs.compare(result.wcs.wcs)
    # And that the headers have equivalent WCS info.
    result_wcs_from_header = WCS(result.header)
    assert result_wcs_from_header.wcs.compare(wcs_out.wcs)


def test_nocelestial_reproject_2D_fail(data_255_delta):

    pytest.importorskip('reproject')

    cube, data = cube_and_raw(data_255_delta)

    proj = cube.moment0(axis=1)

    with pytest.raises(WCSCelestialError) as exc:
        proj.reproject(cube.header)

    assert exc.value.args[0] == ("WCS does not contain two spatial axes.")


@pytest.mark.parametrize('use_memmap', (True,False))
def test_downsample(use_memmap, data_255):
    cube, data = cube_and_raw(data_255)

    dscube = cube.downsample_axis(factor=2, axis=0, use_memmap=use_memmap)

    expected = data.mean(axis=0)

    np.testing.assert_almost_equal(expected[None,:,:],
                                   dscube.filled_data[:].value)

    dscube = cube.downsample_axis(factor=2, axis=1, use_memmap=use_memmap)

    expected = np.array([data[:,:2,:].mean(axis=1),
                         data[:,2:4,:].mean(axis=1),
                         data[:,4:,:].mean(axis=1), # just data[:,4,:]
                        ]).swapaxes(0,1)
    assert expected.shape == (2,3,5)
    assert dscube.shape == (2,3,5)

    np.testing.assert_almost_equal(expected,
                                   dscube.filled_data[:].value)

    dscube = cube.downsample_axis(factor=2, axis=1, truncate=True,
                                  use_memmap=use_memmap)

    expected = np.array([data[:,:2,:].mean(axis=1),
                         data[:,2:4,:].mean(axis=1),
                        ]).swapaxes(0,1)

    np.testing.assert_almost_equal(expected,
                                   dscube.filled_data[:].value)



@pytest.mark.parametrize('use_memmap', (True,False))
def test_downsample_wcs(use_memmap, data_255):
    cube, data = cube_and_raw(data_255)

    dscube = (cube
              .downsample_axis(factor=2, axis=1, use_memmap=use_memmap)
              .downsample_axis(factor=2, axis=2, use_memmap=use_memmap))

    # pixel [0,0] in the new cube should have coordinate [1,1] in the old cube

    lonnew, latnew = dscube.wcs.celestial.wcs_pix2world(0, 0, 0)
    xpixold_ypixold = np.array(cube.wcs.celestial.wcs_world2pix(lonnew, latnew, 0))

    np.testing.assert_almost_equal(xpixold_ypixold, (0.5, 0.5))

    # the center of the bottom-left pixel, in FITS coordinates, in the
    # original frame will now be at -0.25, -0.25 in the new frame
    lonold, latold = cube.wcs.celestial.wcs_pix2world(1, 1, 1)
    xpixnew_ypixnew = np.array(dscube.wcs.celestial.wcs_world2pix(lonold, latold, 1))

    np.testing.assert_almost_equal(xpixnew_ypixnew, (0.75, 0.75))

@pytest.mark.skipif('not tracemallocOK or (sys.version_info.major==3 and sys.version_info.minor<6) or not NPY_VERSION_CHECK')
def test_reproject_3D_memory():

    pytest.importorskip('reproject')

    tracemalloc.start()

    snap1 = tracemalloc.take_snapshot()

    # create a 64 MB cube
    cube,_ = utilities.generate_gaussian_cube(shape=[200,200,200])
    sz = _.dtype.itemsize

    # check that cube is loaded into memory
    snap2 = tracemalloc.take_snapshot()
    diff = snap2.compare_to(snap1, 'lineno')
    diffvals = np.array([dd.size_diff for dd in diff])
    # at this point, the generated cube should still exist in memory
    assert diffvals.max()*u.B >= 200**3*sz*u.B

    wcs_in = cube.wcs
    wcs_out = wcs_in.deepcopy()
    wcs_out.wcs.ctype = ['GLON-SIN', 'GLAT-SIN', cube.wcs.wcs.ctype[2]]
    wcs_out.wcs.crval = [0.001, 0.001, cube.wcs.wcs.crval[2]]
    wcs_out.wcs.crpix = [2., 2., cube.wcs.wcs.crpix[2]]

    header_out = (wcs_out.to_header())
    header_out['NAXIS'] = 3
    header_out['NAXIS1'] = int(cube.shape[2]/2)
    header_out['NAXIS2'] = int(cube.shape[1]/2)
    header_out['NAXIS3'] = cube.shape[0]

    # First the unfilled reprojection test: new memory is allocated for
    # `result`, but nowhere else
    result = cube.reproject(header_out, filled=False)

    snap3 = tracemalloc.take_snapshot()
    diff = snap3.compare_to(snap2, 'lineno')
    diffvals = np.array([dd.size_diff for dd in diff])
    # result should have the same size as the input data, except smaller in two dims
    # make sure that's all that's allocated
    assert diffvals.max()*u.B >= 200*100**2*sz*u.B
    assert diffvals.max()*u.B < 200*110**2*sz*u.B

    # without masking the cube, nothing should change
    result = cube.reproject(header_out, filled=True)

    snap4 = tracemalloc.take_snapshot()
    diff = snap4.compare_to(snap3, 'lineno')
    diffvals = np.array([dd.size_diff for dd in diff])
    assert diffvals.max()*u.B <= 1*u.MB

    assert result.wcs.wcs.crval[0] == 0.001
    assert result.wcs.wcs.crpix[0] == 2.


    # masking the cube will force the fill to create a new in-memory copy
    mcube = cube.with_mask(cube > 0.1*cube.unit)
    # `_is_huge` would trigger a use_memmap
    assert not mcube._is_huge
    assert mcube.mask.any()

    # take a new snapshot because we're not testing the mask creation
    snap5 = tracemalloc.take_snapshot()
    tracemalloc.stop()
    tracemalloc.start() # stop/start so we can check peak mem use from here
    current_b4, peak_b4 = tracemalloc.get_traced_memory()
    result = mcube.reproject(header_out, filled=True)
    current_aftr, peak_aftr = tracemalloc.get_traced_memory()


    snap6 = tracemalloc.take_snapshot()
    diff = snap6.compare_to(snap5, 'lineno')
    diffvals = np.array([dd.size_diff for dd in diff])
    # a duplicate of the cube should have been created by filling masked vals
    # (this should be near-exact since 'result' should occupy exactly the
    # same amount of memory)
    assert diffvals.max()*u.B <= 1*u.MB #>= 200**3*sz*u.B
    # the peak memory usage *during* reprojection will have that duplicate,
    # but the memory gets cleaned up afterward
    assert (peak_aftr-peak_b4)*u.B >= (200**3*sz*u.B + 200*100**2*sz*u.B)

    assert result.wcs.wcs.crval[0] == 0.001
    assert result.wcs.wcs.crpix[0] == 2.
