from __future__ import (division, print_function, absolute_import,
                        unicode_literals)

from collections import OrderedDict
import os

from IPython.html import widgets

from astropy.modeling import models
from astropy.io import fits
import ccdproc

import numpy as np

from . import gui

__all__ = [
    'ReductionWidget',
    'CombinerWidget',
    'CosmicRaySettingsWidget',
    'SliceWidget',
    'CalibrationStepWidget',
    'OverscanWidget',
    'TrimWidget'
]


class ReducerBase(gui.ToggleGoWidget):
    """
    Base class for reduction and combination widgets that provides a couple
    of properties common to both.
    """
    def __init__(self, *arg, **kwd):
        self._apply_to = kwd.pop('apply_to', None)
        self._destination = kwd.pop('destination', None)
        super(ReducerBase, self).__init__(*arg, **kwd)

    @property
    def destination(self):
        return self._destination

    @property
    def apply_to(self):
        return self._apply_to


class ReductionWidget(ReducerBase):
    """docstring for ReductionWidget"""
    def __init__(self, *arg, **kwd):
        allow_flat = kwd.pop('allow_flat', True)
        allow_dark = kwd.pop('allow_dark', True)
        allow_bias = kwd.pop('allow_bias', True)
        self.image_collection = kwd.pop('input_image_collection', None)
        self._master_source = kwd.pop('master_source', None)
        super(ReductionWidget, self).__init__(*arg, **kwd)
        self._overscan = OverscanWidget(description='Subtract overscan?')
        self._trim = TrimWidget(description='Trim (specify region to keep)?')
        self._cosmic_ray = CosmicRaySettingsWidget()
        self._bias_calib = BiasSubtractWidget(master_source=self._master_source)
        self._dark_calib = DarkSubtractWidget(description="Subtract dark?")
        self._flat_calib = CalibrationStepWidget(description="Flat correct?")
        self.add_child(self._overscan)
        self.add_child(self._trim)
        self.add_child(self._cosmic_ray)

        if allow_bias:
            self.add_child(self._bias_calib)
        if allow_dark:
            self.add_child(self._dark_calib)
        if allow_flat:
            self.add_child(self._flat_calib)

    @property
    def reduced_images(self):
        """
        List of reduced images; each image is a `ccdproc.CCDData`` object.
        """
        return self._reduced_images

    def action(self):
        if not self.image_collection:
            raise ValueError("No images to reduce")
        reduced_images = []
        for hdu, fname in self.image_collection.hdus(return_fname=True,
                                                     save_location=self.destination,
                                                     **self.apply_to):
            ccd = ccdproc.CCDData(data=hdu.data, meta=hdu.header, unit="adu")
            for child in self.container.children:
                if not child.toggle.value:
                    # Nothing to do for this child, so keep going.
                    continue
                ccd = child.action(ccd)
            hdu_tmp = ccd.to_hdu()[0]
            print(ccd.shape)
            hdu.header = hdu_tmp.header
            hdu.data = hdu_tmp.data
            reduced_images.append(ccd)
        self._reduced_images = reduced_images


class ClippingWidget(gui.ToggleContainerWidget):
    """docstring for ClippingWidget"""
    def __init__(self, *args, **kwd):
        super(ClippingWidget, self).__init__(*args, **kwd)
        self._min_max = gui.ToggleMinMaxWidget(description="Clip by min/max?")
        self._sigma_clip = gui.ToggleMinMaxWidget(description="Sigma clip?")
        self.add_child(self._min_max)
        self.add_child(self._sigma_clip)

    @property
    def is_sane(self):
        # If not selected, sanity state does not matter...
        if not self.toggle.value:
            return None

        # It makes no sense to have selected clipping but not a clipping
        # method....
        sanity = (self._min_max.toggle.value or
                  self._sigma_clip.toggle.value)

        # For min_max clipping, maximum must be greater than minimum.
        if self._min_max.toggle.value:
            sanity = sanity and (self._min_max.max > self._min_max.min)

        # For sigma clipping there is no relationship  between maximum
        # and minimum because both are number of deviations above/below
        # central value, but values of 0 make no sense

        if self._sigma_clip.toggle.value:
            sanity = (sanity and
                      self._sigma_clip.min != 0 and
                      self._sigma_clip.max != 0)

        return sanity

    def format(self):
        super(ClippingWidget, self).format()
        self._sigma_clip.format()
        self._min_max.format()


class CombineWidget(gui.ToggleContainerWidget):
    """
    Represent combine choices and actions.
    """
    def __init__(self, *args, **kwd):
        super(CombineWidget, self).__init__(*args, **kwd)
        self._combine_option = \
            widgets.ToggleButtonsWidget(description="Combination method:",
                                        values=['Average', 'Median'])
        self.add_child(self._combine_option)
        self._scaling = gui.ToggleContainerWidget(description="Scale before combining?")
        scal_desc = "Which property should scale to same value?"
        self._scale_by = widgets.RadioButtonsWidget(description=scal_desc,
                                                    values=['mean', 'median'])
        self._scaling.add_child(self._scale_by)
        self.add_child(self._scaling)

    @property
    def method(self):
        return self._combine_option.value

    @property
    def scaling_func(self):
        if not self._scaling.toggle.value:
            return None
        if self._scale_by == 'mean':
            return lambda arr: 1/np.ma.average(arr)
        elif self._scale_by == 'median':
            return lambda arr: 1/np.ma.median(arr)

    @property
    def is_sane(self):
        if not self.toggle.value:
            return None
        else:
            # In this case, the only options presented are sane ones
            return True


class GroupByWidget(gui.ToggleContainerWidget):
    def __init__(self, *args, **kwd):
        self._image_source = kwd.pop('image_source', None)
        input_value = kwd.pop('value', '')
        super(GroupByWidget, self).__init__(*args, **kwd)
        self._keyword_list = widgets.TextWidget(value=input_value)
        self.add_child(self._keyword_list)
        if input_value:
            self.toggle.value = True

    @property
    def value(self):
        return self._keyword_list.value

    def groups(self, apply_to):
        if not (self.toggle.value and self.value):
            # Return an empty dictionary by default if there is no grouping
            return [{}]

        # remember, the rest is really an else to the above...
        from copy import deepcopy
        keywords = self.value.split(',')
        # Yuck...need to use an internal method to get the mask I need.
        tmp_coll = deepcopy(self._image_source)
        tmp_coll._find_keywords_by_values(**apply_to)
        mask = tmp_coll.summary_info['file'].mask
        # Note the logical not below; mask indicates which values
        # should be EXCLUDED.
        filtered_table = tmp_coll.summary_info[~mask]
        grouped_table = filtered_table.group_by(keywords)
        combine_groups = grouped_table.groups.keys
        group_list = []
        for row in combine_groups:
            d = {c: row[c] for c in combine_groups.colnames}
            group_list.append(d)

        print(group_list)
        return group_list


class CombinerWidget(ReducerBase):
    """
    Widget for displaying options for ccdproc.Combiner.

    Parameters
    ----------

    description : str, optional
        Text displayed next to check box for selecting options.
    """
    def __init__(self, *args, **kwd):
        group_by_in = kwd.pop('group_by', '')
        self._image_source = kwd.pop('image_source', None)
        super(CombinerWidget, self).__init__(*args, **kwd)
        self._clipping_widget = \
            ClippingWidget(description="Clip before combining?")
        self._combine_method = \
            CombineWidget(description="Combine images?")

        self.add_child(self._clipping_widget)
        self.add_child(self._combine_method)

        self._group_by = GroupByWidget(description='Group by:',
                                       value=group_by_in,
                                       image_source=self._image_source)
        self.add_child(self._group_by)

        self._combined = None

    @property
    def combined(self):
        """
        The combined image.
        """
        return self._combined

    @property
    def image_source(self):
        return self._image_source

    @property
    def is_sane(self):
        # Start with the default sanity determination...
        sanity = super(CombinerWidget, self).is_sane
        # ...but flip to insane if neither clipping nor combination is
        # selected.
        sanity = sanity and (self._clipping_widget.toggle.value
                             or self._combine_method.toggle.value)
        return sanity

    def format(self):
        super(CombinerWidget, self).format()
        self._clipping_widget.format()

    def action(self):
        for combo_group in self._group_by.groups(self.apply_to):
            combined = self._action_for_one_group(combo_group)
            fname = ['_'.join([str(k), str(v)]) for k, v in combo_group.iteritems()]
            fname = 'master_bias_' + '_'.join(fname) + '.fit'
            print(fname)
            dest_path = os.path.join(self.destination, fname)
            combined.write(dest_path)
            self._combined = combined

    def _action_for_one_group(self, filter_dict=None):
        combined_dict = self.apply_to.copy()
        if filter_dict is not None:
            combined_dict.update(filter_dict)

        images = []

        for hdu in self.image_source.hdus(**combined_dict):
            images.append(ccdproc.CCDData(data=hdu.data,
                                          meta=hdu.header,
                                          unit="adu"))
        combiner = ccdproc.Combiner(images)
        if self._clipping_widget.toggle.value:
            if self.min_max.value:
                combiner.minmax_clipping(min_clip=self.min_max.min,
                                         max_clip=self.min_max.max)
            if self.sigma_clip.value:
                combiner.sigma_clipping(low_thresh=self.sigma_clip.min,
                                        high_thresh=self.sigma_clip.max)
        if self._combine_method.method == 'Average':
            print("Averaging")
            combined = combiner.average_combine()
        elif self._combine_method.method == 'Median':
            print("Median-ing")
            combined = combiner.median_combine()
        combined.header = images[0].header
        combined.header['master'] = True
        return combined


class CosmicRaySettingsWidget(gui.ToggleContainerWidget):
    def __init__(self, *args, **kwd):
        descript = kwd.pop('description', 'Clean cosmic rays?')
        kwd['description'] = descript
        super(CosmicRaySettingsWidget, self).__init__(*args, **kwd)
        cr_choices = widgets.DropdownWidget(
            description='Method:',
            values={'median': None, 'LACosmic': None}
        )
        self.add_child(cr_choices)

    def display(self):
        from IPython.display import display
        display(self)


class SliceWidget(gui.ToggleContainerWidget):
    def __init__(self, *arg, **kwd):
        self.images = kwd.pop('images', [])
        super(SliceWidget, self).__init__(*arg, **kwd)
        drop_desc = ('Region is along all of')
        self._axis_selection = widgets.ContainerWidget()
        values = OrderedDict()
        values["axis 0"] = 0
        values["axis 1"] = 1
        self._pre = widgets.ToggleButtonsWidget(description=drop_desc,
                                                values=values)
        self._start = widgets.IntTextWidget(description='and on the other axis from index ')
        self._stop = widgets.IntTextWidget(description='up to (but not including):')
        self._axis_selection.children = [
            self._pre,
            self._start,
            self._stop
        ]
        self.add_child(self._axis_selection)
        for child in self._axis_selection.children:
            self._child_notify_parent_on_change(child)

    def format(self):
        super(SliceWidget, self).format()
        hbox_these = [self._axis_selection]  # [self, self.container]
        for hbox in hbox_these:
            hbox.remove_class('vbox')
            hbox.add_class('hbox')
        self._start.set_css('width', '30px')
        self._stop.set_css('width', '30px')

    @property
    def is_sane(self):
        """
        Determine whether combination of settings is at least remotely
        plausible.
        """
        # If the SliceWidget is not selected, return None
        if not self.toggle.value:
            return None
        # Stop value must be larger than start (i.e. slice must contain at
        # least one row/column).
        sanity = self._stop.value > self._start.value
        return sanity


class CalibrationStepWidget(gui.ToggleContainerWidget):
    """
    Represents a calibration step that corresponds to a ccdproc command.

    Parameters
    ----------

    None
    """
    def __init__(self, *args, **kwd):
        self._master_source = kwd.pop('master_source', None)
        super(CalibrationStepWidget, self).__init__(*args, **kwd)
        self._source_dict = {'Created in this notebook': 'notebook',
                             'File on disk': 'disk'}
        self._settings = \
            widgets.ContainerWidget(description="Reduction choices")

        self._source = widgets.ToggleButtonsWidget(description='Source:',
                                                   values=self._source_dict)
        self._file_select = widgets.DropdownWidget(description="Select file:",
                                                   values=["Not working yet"],
                                                   visible=False)
        self._settings.children = [self._source, self._file_select]
        self.add_child(self._settings)
        self._source.on_trait_change(self._file_select_visibility(),
                                     str('value_name'))

    def _file_select_visibility(self):
        def file_visibility(name, value):
            self._file_select.visible = self._source_dict[value] == 'disk'
        return file_visibility

    def _master_image(self, **selector):
        """
        Identify appropriate master and return as `ccdproc.CCDData`.
        """
        if not self._master_source:
            raise RuntimeError("No source provided for master.")
        file_name = self._master_source.files_filtered(master=True,
                                                       **selector)
        if len(file_name) != 1:
            raise RuntimeError("Well, crap. Should only be one master.")
        file_name = file_name[0]
        path = os.path.join(self._master_source.location, file_name)
        return ccdproc.CCDData.read(path, unit="adu")


class BiasSubtractWidget(CalibrationStepWidget):
    """
    Subtract bias from an image using widget settings.
    """
    def __init__(self, bias_image=None, **kwd):
        desc = kwd.pop('description', 'Subtract bias?')
        kwd['description'] = desc
        super(BiasSubtractWidget, self).__init__(**kwd)
        if self._master_source:
            self.master_bias = self._master_image(**{'imagetyp': 'bias'})
        else:
            self.master_bias = None

    def action(self, ccd):
        return ccdproc.subtract_bias(ccd, self.master_bias)


class DarkSubtractWidget(CalibrationStepWidget):
    """
    Subtract dark from an image using widget settings.
    """
    def __init__(self, bias_image=None, **kwd):
        desc = kwd.pop('description', 'Subtract Dark?')
        kwd['description'] = desc
        super(DarkSubtractWidget, self).__init__(**kwd)
        if self._master_source:
            self.master = self._master_image(**{'imagetyp': 'dark'})
        else:
            self.master = None

    def action(self, ccd):
        return ccdproc.subtract_dark(ccd, self.master)


class OverscanWidget(SliceWidget):
    """docstring for OverscanWidget"""
    def __init__(self, *arg, **kwd):
        super(OverscanWidget, self).__init__(*arg, **kwd)
        poly_desc = "Fit polynomial to overscan?"
        self._polyfit = gui.ToggleContainerWidget(description=poly_desc)
        poly_values = OrderedDict()
        poly_values["Order 0/one term (constant)"] = 1
        poly_values["Order 1/two term (linear)"] = 2
        poly_values["Order 2/three team (quadratic)"] = 3
        poly_values["Are you serious? Higher order is silly."] = None
        poly_dropdown = widgets.DropdownWidget(description="Choose fit",
                                               values=poly_values,
                                               value=1)
        self._polyfit.add_child(poly_dropdown)
        self.add_child(self._polyfit)

    def format(self):
        super(OverscanWidget, self).format()
        self._polyfit.format()
        self._polyfit.remove_class('vbox')
        self._polyfit.add_class('hbox')

    @property
    def is_sane(self):
        # Am I even active? If not, return None
        if not self.toggle.value:
            return None

        # See what the SliceWidget thinks....
        sanity = super(OverscanWidget, self).is_sane
        if self._polyfit.toggle.value:
            poly_dropdown = self._polyfit.container.children[0]
            sanity = sanity and (poly_dropdown.value is not None)
        return sanity

    @property
    def polynomial_order(self):
        # yuck
        return self._polyfit.container.children[0].value

    def action(self, ccd):
        """
        Subtract overscan from image based on settings.

        Parameters
        ----------

        ccd : `ccdproc.CCDData`
            Image to be reduced.
        """
        if not self.toggle.value:
            pass

        whole_axis = slice(None, None)
        partial_axis = slice(self._start.value, self._stop.value)
        # create a two-element list which will be filled with the appropriate
        # slice based on the widget settings.
        if self._pre.value == 0:
            first_axis = whole_axis
            second_axis = partial_axis
            oscan_axis = 1
        else:
            first_axis = partial_axis
            second_axis = whole_axis
            oscan_axis = 0

        if self._polyfit.toggle.value:
            poly_model = models.Polynomial1D(self.polynomial_order)
        else:
            poly_model = None

        reduced = ccdproc.subtract_overscan(ccd,
                                            overscan=ccd[first_axis, second_axis],
                                            overscan_axis=oscan_axis,
                                            model=poly_model)
        return reduced


class TrimWidget(SliceWidget):
    """
    Controls and action for trimming a widget.
    """
    def __init__(self, *arg, **kwd):
        super(TrimWidget, self).__init__(*arg, **kwd)

    def action(self, ccd):
        """
        Trim an image to bounds given in the widget.

        Returns
        -------

        trimmed : `ccdproc.CCDData`
            Trimmed image.
        """
        # Don't do anything if not activated
        if not self.toggle.value:
            pass
        whole_axis = slice(None, None)
        partial_axis = slice(self._start.value, self._stop.value)
        # create a two-element list which will be filled with the appropriate
        # slice based on the widget settings.
        if self._pre.value == 0:
            trimmed = ccdproc.trim_image(ccd[whole_axis, partial_axis])
        else:
            trimmed = ccdproc.trim_image(ccd[partial_axis, whole_axis])

        return trimmed
